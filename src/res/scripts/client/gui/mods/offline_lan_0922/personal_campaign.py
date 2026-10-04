"""Regular personal-mission rewards from the installed client's resources.

Completion, paid reward stages and delayed crew claims are separate facts.
Launcher edits and battle receipts enter the same idempotent settlement path.
"""
from __future__ import print_function

import copy
import time

from gui.mods.offline_lan_0922.account_rpc import data
from gui.mods.offline_lan_0922.account_rpc.garage import GarageError

RESOURCE_ROOT = 'scripts/item_defs/potapov_quests/'
TOKEN_EXPIRY = 4104777660
_resources = {}


def children(node, name):
    return [item for tag, item in (node or {}).get('children', ()) if tag == name]


def child(node, name):
    found = children(node, name)
    return found[0] if found else None


def value(node, name=None, default=''):
    item = child(node, name) if name is not None else node
    return item.get('value', default) if item is not None else default


def _node(section):
    return {'value': section.asString.strip(),
            'children': [(name, _node(item)) for name, item in section.items()]}


def _resource(path, open_section=None):
    if open_section is None:
        if path in _resources:
            return _resources[path]
        import ResMgr
        section = ResMgr.openSection(RESOURCE_ROOT + path)
    else:
        section = open_section(RESOURCE_ROOT + path)
    if section is None:
        raise GarageError('PERSONAL_MISSION_RESOURCE_UNAVAILABLE: ' + path)
    result = _node(section)
    if open_section is None:
        _resources[path] = result
    return result


def mission_definition(mission_id, open_section=None):
    mission_id = int(mission_id)
    if not 1 <= mission_id <= 300:
        raise GarageError('INVALID_PERSONAL_MISSION')
    operation, offset = divmod(mission_id - 1, 75)
    chain, index = divmod(offset, 15)
    name = 'regular_%d_%d_%d' % (operation + 1, chain + 1, index + 1)
    path = 'regular/tile_%d/chain_%d/%s.xml' % (
        operation + 1, chain + 1, name)
    resource = _resource(path, open_section)
    quests = child(resource, 'quests')
    result = {'metadata': child(_resource('list.xml', open_section), name)}
    stages = ('main', 'main_award_list', 'add', 'add_award_list')
    quest_rows = children(quests, 'potapovQuest')
    if len(quest_rows) != 4 or result['metadata'] is None:
        raise GarageError('INVALID_PERSONAL_MISSION_RESOURCE: ' + name)
    # PQCache consumes the four entries in resource order and checks each
    # suffix. Their unique IDs need not duplicate the containing file name.
    for stage, quest in zip(stages, quest_rows):
        if not value(quest, 'id').endswith(stage):
            raise GarageError('INVALID_PERSONAL_MISSION_STAGE: ' + name)
        result[stage] = quest
    return result


def _tokens(snapshot):
    raw = snapshot.get('personalMissionTokens') or {}
    result = {}
    for name, row in raw.items():
        try:
            expiry, count = row
            result[str(name)] = [int(expiry), max(0, int(count))]
        except (TypeError, ValueError):
            continue
    return result


def _grant_vehicle(state, name):
    from gui.mods.offline_lan_0922 import personal_campaign_vehicles

    def create():
        # The first claim carries the XML's stock hull and 100% crew. A
        # withdrawn claim restores its held hull without creating this crew
        # again; an already owned hull receives the full purchase-price value.
        from items import ITEM_TYPE_INDICES
        from gui.mods.offline_lan_0922 import bootstrap
        compact_descr = bootstrap._build_purchased_vehicle(
            state.snapshot(), state._vehicles_module(), state._tankmen_module(),
            ITEM_TYPE_INDICES, state._default_vehicle_settings(), name)
        for record in state._records():
            if int(record.get('vehicleTypeCompactDescr', 0)) == compact_descr:
                state._touched_tankmen.update(record.get('tankmen', {}))
        return compact_descr

    return personal_campaign_vehicles.grant(state, name, create)


def _grant_bonus(state, bonus, now):
    snapshot = state.snapshot()
    vehicle_effects = []
    for name, item in (bonus or {}).get('children', ()):
        amount = value(item)
        if name in ('credits', 'gold', 'freeXP', 'crystal'):
            wallet = state._wallet()
            wallet[name] = wallet.get(name, 0) + int(amount)
        elif name in ('slots', 'berths'):
            key = 'accountSlots' if name == 'slots' else 'accountBerths'
            snapshot[key] = int(snapshot.get(key, 0)) + int(amount)
        elif name == 'premium':
            snapshot['premiumExpiryTime'] = max(
                now, int(snapshot.get('premiumExpiryTime', 0))) + int(amount) * 86400
        elif name == 'item':
            compact_descr, count = int(amount), int(value(item, 'count', '1'))
            item_type = state._item_type(compact_descr)
            # Resolve before changing stock: unknown items leave this stage
            # pending instead of marking a reward paid without delivery.
            state._vehicles_module().getItemByCompactDescr(compact_descr)
            old = snapshot.get('inventoryItems', {}).get(item_type, {}).get(compact_descr, 0)
            state._set_owned(compact_descr, item_type, int(old) + count)
        elif name == 'token':
            identifier, count = value(item, 'id'), int(value(item, 'count', '1'))
            if identifier == 'free_award_list':
                snapshot['personalMissionOrders'] = data.personal_mission_orders(
                    snapshot.get('personalMissionOrders', 0) + count)
            else:
                tokens = _tokens(snapshot)
                old = tokens.get(identifier, [TOKEN_EXPIRY, 0])[1]
                limit = int(value(item, 'limit', '2147483647'))
                tokens[identifier] = [TOKEN_EXPIRY, min(limit, old + count)]
                snapshot['personalMissionTokens'] = tokens
        elif name == 'vehicle':
            effect = _grant_vehicle(state, amount)
            if isinstance(effect, dict):
                vehicle_effects.append(effect)
        elif name == 'dossier':
            identifier = value(item, 'name')
            raw = value(item, 'value')
            count = now if raw == 'timestamp' else int(raw)
            if identifier.startswith('playerBadges:'):
                snapshot.setdefault('accountBadges', {})[identifier.split(':', 1)[1]] = count
            else:
                dossier = snapshot.setdefault('personalMissionDossier', {})
                dossier[identifier] = (int(dossier.get(identifier, 0)) + count
                                       if value(item, 'type') == 'add' else count)
        elif name == 'customizations':
            from items.components import c11n_constants
            kinds = {'camouflage': c11n_constants.CustomizationType.CAMOUFLAGE}
            for row in children(item, 'item'):
                kind = kinds.get(value(row, 'custType'))
                if kind is None:
                    raise GarageError('UNSUPPORTED_PERSONAL_MISSION_CUSTOMIZATION')
                vehicle = state._vehicles_module().VehicleDescr(typeName=value(row, 'boundVehicle'))
                nation, vehicle_id = vehicle.type.id
                bound = state._vehicles_module().makeIntCompactDescrByID('vehicle', nation, vehicle_id)
                buckets = snapshot.setdefault('customizationItems', {}).setdefault(
                    kind, {}).setdefault(int(value(row, 'id')), {})
                buckets[bound] = buckets.get(bound, 0) + int(value(row, 'value'))
        else:
            raise GarageError('UNSUPPORTED_PERSONAL_MISSION_REWARD: ' + name)
    return {'vehicles': vehicle_effects}


def _definition(mission_id, definitions=None, open_section=None):
    return (definitions[int(mission_id)] if definitions is not None
            else mission_definition(int(mission_id), open_section))


def _reward_rows(receipt, economic_rows=None, vehicle_rows=None):
    """Report actual delivered effects, without counting compensation twice."""
    rows = copy.deepcopy(receipt.get('rewards', ()) if economic_rows is None
                         else economic_rows)
    vehicles = copy.deepcopy(receipt.get('vehicles', ()) if vehicle_rows is None
                             else vehicle_rows)
    # A reset can reclaim only the credits left after spending. Attribute
    # that actual withdrawal to compensation before ordinary credit rewards.
    available = sum(int(row.get('count', 0)) for row in rows
                    if row.get('kind') == 'credits')
    for row in vehicles:
        if row.get('kind') == 'compensation':
            row['credits'] = min(available, max(0, int(row.get('credits', 0))))
            available -= row['credits']
    vehicles = [row for row in vehicles if row.get('kind') != 'compensation'
                or row.get('credits')]
    compensation = sum(int(row.get('credits', 0)) for row in vehicles
                       if row.get('kind') == 'compensation')
    for row in rows:
        if row.get('kind') == 'credits' and compensation:
            deducted = min(compensation, int(row.get('count', 0)))
            row['count'] = int(row.get('count', 0)) - deducted
            compensation -= deducted
    rows = [row for row in rows if row.get('count', 1)]
    rows.extend(vehicles)
    rows.extend(copy.deepcopy(receipt.get('token_rewards') or ()))
    return rows


def _record_reward(state, bonus, now):
    from gui.mods.offline_lan_0922 import personal_campaign_rewards
    previous = _tokens(state.snapshot())
    receipt = personal_campaign_rewards.record_grant(
        state, bonus, now, lambda: _grant_bonus(state, bonus, now))
    current = _tokens(state.snapshot())
    receipt['token_rewards'] = [
        {'kind': 'token', 'id': name, 'count': row[1] - previous.get(name, [0, 0])[1]}
        for name, row in current.items()
        if (name.startswith('token:pt:final:') and len(name.split(':')) == 5 and
            row[1] > previous.get(name, [0, 0])[1])]
    return receipt


def _legacy_reward(state, bonus, now):
    from gui.mods.offline_lan_0922 import personal_campaign_rewards
    receipt = personal_campaign_rewards.legacy_receipt(state, bonus, now)
    if children(bonus, 'vehicle'):
        from gui.mods.offline_lan_0922 import personal_campaign_vehicles
        receipt['vehicles'] = [personal_campaign_vehicles.legacy_effect(
            state, value(row)) for row in children(bonus, 'vehicle')]
    return receipt


def _revoke_reward(state, receipt, now):
    from gui.mods.offline_lan_0922 import personal_campaign_rewards
    vehicle_rows = []
    if receipt.get('vehicles'):
        from gui.mods.offline_lan_0922 import personal_campaign_vehicles
        for effect in reversed(receipt['vehicles']):
            withdrawn = personal_campaign_vehicles.revoke(state, effect)
            if isinstance(withdrawn, dict):
                vehicle_rows.append(withdrawn)
    withdrawn = personal_campaign_rewards.revoke(state, receipt, now)
    return _reward_rows(receipt, withdrawn, vehicle_rows)


def _operation_entitlements(progress, definitions=None, open_section=None):
    """Replay installed token dependencies using only current task progress."""
    tokens = {}
    for key, level in progress.items():
        definition = _definition(key, definitions, open_section)
        for stage in ('main', 'add')[:level]:
            for row in children(child(definition[stage], 'bonus'), 'token'):
                name = value(row, 'id')
                if name != 'free_award_list':
                    tokens[name] = tokens.get(name, 0) + int(value(row, 'count', '1'))
    quests = children(child(_resource('tiles.xml', open_section), 'quests'), 'tokenQuest')
    eligible = []
    while True:
        advanced = False
        for quest in quests:
            identifier = value(quest, 'id')
            conditions = child(child(child(quest, 'conditions'), 'preBattle'), 'account')
            requirements = children(conditions, 'token')
            if (identifier in eligible or value(quest, 'enabled') != 'true' or
                    not requirements or any(tag != 'token' for tag, unused in conditions['children']) or
                    any(tokens.get(value(row, 'id'), 0) < int(value(row, 'greaterOrEqual', '1'))
                        for row in requirements)):
                continue
            eligible.append(identifier)
            for row in children(child(quest, 'bonus'), 'token'):
                name = value(row, 'id')
                if name != 'free_award_list':
                    tokens[name] = min(int(value(row, 'limit', '2147483647')),
                        tokens.get(name, 0) + int(value(row, 'count', '1')))
            for row in requirements:
                name = value(row, 'id')
                tokens[name] = max(0, tokens.get(name, 0) - int(value(row, 'consume', '0')))
            advanced = True
        if not advanced:
            return eligible, dict((value(row, 'id'), row) for row in quests)


def _rebuild_progress_tokens(state, progress, definitions=None, open_section=None):
    """Recompute counters from tasks and the operation claims still earned."""
    totals = {}
    for key, level in progress.items():
        definition = (definitions[int(key)] if definitions is not None
                      else mission_definition(int(key), open_section))
        for stage in ('main', 'add')[:level]:
            for row in children(child(definition[stage], 'bonus'), 'token'):
                name = value(row, 'id')
                if name != 'free_award_list':
                    totals[name] = totals.get(name, 0) + int(value(row, 'count', '1'))
    # Badge entitlement needs unspent task counters. Account publication also
    # includes the bonuses and token consumption of still-earned operations.
    progress_totals = dict(totals)
    claimed = set(state.snapshot().get('personalMissionTokenRewards') or ())
    if claimed:
        for quest in children(child(_resource('tiles.xml', open_section), 'quests'), 'tokenQuest'):
            if value(quest, 'id') not in claimed:
                continue
            for row in children(child(quest, 'bonus'), 'token'):
                name = value(row, 'id')
                if name != 'free_award_list':
                    totals[name] = totals.get(name, 0) + int(value(row, 'count', '1'))
            account = child(child(child(quest, 'conditions'), 'preBattle'), 'account')
            for row in children(account, 'token'):
                name = value(row, 'id')
                totals[name] = totals.get(name, 0) - int(value(row, 'consume', '0'))
    tokens = dict((name, [TOKEN_EXPIRY, max(0, count)]) for name, count in totals.items())
    if tokens != _tokens(state.snapshot()):
        state.snapshot()['personalMissionTokens'] = tokens
        state.revision += 1
    return progress_totals


def _reconcile_campaign_badges(state, progress_totals, now, open_section=None):
    """Replay only token dependencies to derive current campaign cosmetics.

    Dependent badge quests use the current entitlement of their source quests.
    Legacy or manually edited badge ownership cannot replace that entitlement.
    """
    snapshot = state.snapshot()
    if not (progress_totals or snapshot.get('accountBadges') or
            snapshot.get('selectedBadges')):
        return
    quests = children(child(_resource('tiles.xml', open_section), 'quests'), 'tokenQuest')
    managed, eligible = set(), set()
    rows = []
    for quest in quests:
        badge_ids = set(value(row, 'name').split(':', 1)[1]
                        for row in children(child(quest, 'bonus'), 'dossier')
                        if value(row, 'name').startswith('playerBadges:'))
        managed.update(badge_ids)
        conditions = child(child(child(quest, 'conditions'), 'preBattle'), 'account')
        requirements = children(conditions, 'token')
        if (value(quest, 'enabled') == 'true' and requirements and
                all(tag == 'token' for tag, unused in conditions['children'])):
            rows.append((quest, requirements, badge_ids))
    tokens = dict(progress_totals)
    completed = set()
    while True:
        advanced = False
        for index, (quest, requirements, badge_ids) in enumerate(rows):
            if index in completed or any(
                    tokens.get(value(row, 'id'), 0) < int(value(row, 'greaterOrEqual', '1'))
                    for row in requirements):
                continue
            completed.add(index)
            eligible.update(badge_ids)
            for row in children(child(quest, 'bonus'), 'token'):
                name = value(row, 'id')
                if name != 'free_award_list':
                    tokens[name] = min(int(value(row, 'limit', '2147483647')),
                                       tokens.get(name, 0) + int(value(row, 'count', '1')))
            for row in requirements:
                name = value(row, 'id')
                tokens[name] = max(0, tokens.get(name, 0) - int(value(row, 'consume', '0')))
            advanced = True
        if not advanced:
            break
    badges = data.account_badges(snapshot.get('accountBadges'))
    revoked = managed - eligible
    for badge in revoked:
        badges.pop(badge, None)
    for badge in eligible:
        if badge not in badges:
            badges[badge] = now
    selected = [badge for badge in (snapshot.get('selectedBadges') or ())
                if str(badge) not in revoked]
    if badges != snapshot.get('accountBadges', {}):
        snapshot['accountBadges'] = badges
        state.revision += 1
    if selected != list(snapshot.get('selectedBadges') or ()):
        snapshot['selectedBadges'] = selected
        state.revision += 1


def saved_fields(snapshot):
    """JSON fields shared by startup, receipt settlement and launcher edits."""
    progress = data.personal_mission_completed(snapshot.get('personalMissionProgress'))
    rewarded = data.personal_mission_completed(snapshot.get('personalMissionRewarded'))
    result = {
        # Claim markers and actual reward receipts move together during an
        # atomic reset. Source records for parked hulls remain available.
        'rewarded': rewarded,
        'pawned': dict((str(key), int(count)) for key, count in
                       (snapshot.get('personalMissionPawned') or {}).items()
                       if str(key) in progress and int(count) in (1, 4)),
        'tankwomen': dict((str(key), True) for key, claimed in
                          (snapshot.get('personalMissionTankwomen') or {}).items()
                          if claimed and str(key) in progress),
        'tokens': _tokens(snapshot),
        'tokenRewards': list(snapshot.get('personalMissionTokenRewards') or ()),
        'dossier': dict(snapshot.get('personalMissionDossier') or {}),
        'rewardJournal': dict(snapshot.get('personalMissionRewardJournal') or {}),
        'notifications': copy.deepcopy(snapshot.get('personalMissionNotifications') or []),
        'resetError': snapshot.get('personalMissionResetError', ''),
    }
    for source, target in (('personalMissionRequestedCompleted', 'requestedCompleted'),
                           ('personalMissionRequestedRegular', 'requestedRegular')):
        if source in snapshot:
            result[target] = snapshot[source]
    return result


def restored_fields(personal_missions):
    mapping = {'rewarded': 'personalMissionRewarded', 'pawned': 'personalMissionPawned',
               'tankwomen': 'personalMissionTankwomen', 'tokens': 'personalMissionTokens',
               'tokenRewards': 'personalMissionTokenRewards', 'dossier': 'personalMissionDossier',
               'rewardJournal': 'personalMissionRewardJournal', 'resetError': 'personalMissionResetError',
               'notifications': 'personalMissionNotifications',
               'requestedCompleted': 'personalMissionRequestedCompleted',
               'requestedRegular': 'personalMissionRequestedRegular'}
    return dict((target, personal_missions[source])
                for source, target in mapping.items() if source in personal_missions)


def _token_quests(state, now, open_section=None, operations=None):
    snapshot = state.snapshot()
    if not snapshot.get('personalMissionTokens'):
        return []
    completed = set(snapshot.get('personalMissionTokenRewards') or ())
    granted = []
    for quest in children(child(_resource('tiles.xml', open_section), 'quests'), 'tokenQuest'):
        identifier = value(quest, 'id')
        if identifier in completed or value(quest, 'enabled') != 'true':
            continue
        conditions = child(child(child(quest, 'conditions'), 'preBattle'), 'account')
        requirements = children(conditions, 'token')
        if not requirements or any(tag != 'token' for tag, unused in conditions['children']):
            continue
        tokens = _tokens(snapshot)
        if any(tokens.get(value(row, 'id'), [0, 0])[1] < int(value(row, 'greaterOrEqual', '1'))
               for row in requirements):
            continue
        with state._transaction():
            receipt = _record_reward(state, child(quest, 'bonus'), now)
            snapshot.setdefault('personalMissionRewardJournal', {})[
                'operation:' + identifier] = receipt
            tokens = _tokens(snapshot)
            for row in requirements:
                token = value(row, 'id')
                tokens[token][1] = max(0, tokens[token][1] - int(value(row, 'consume', '0')))
            snapshot['personalMissionTokens'] = tokens
            completed.add(identifier)
            snapshot['personalMissionTokenRewards'] = sorted(completed)
            state.revision += 1
        granted.append(identifier)
        if operations is not None:
            operations.append({'id': identifier, 'phase': 'granted',
                               'rewards': _reward_rows(receipt)})
    return granted


def _apply_requested_progress(state, now=None, definitions=None,
                              open_section=None, notices=None):
    """Reverse every cancelled reward atomically before replacing progress."""
    snapshot = state.snapshot()
    if 'personalMissionRequestedCompleted' not in snapshot:
        return ''
    from gui.mods.offline_lan_0922 import personal_campaign_ledger
    now = int(time.time() if now is None else now)
    requested = data.personal_mission_completed(snapshot['personalMissionRequestedCompleted'])
    previous = data.personal_mission_completed(snapshot.get('personalMissionProgress'))
    revoked_missions, revoked_operations = [], []
    try:
        with state._transaction():
            journal = snapshot.setdefault('personalMissionRewardJournal', {})
            rewarded = data.personal_mission_completed(snapshot.get('personalMissionRewarded'))
            returned_orders = {}
            # Return only orders from missions cancelled in this same edit
            # before reclaiming earnings, so dictionary order cannot reject
            # an otherwise balanced reset.
            for key in previous:
                if requested.get(key, 0) == 0:
                    before_orders = order_balance(snapshot)
                    snapshot.setdefault('personalMissionPawned', {}).pop(key, 0)
                    balance = _reconcile_order_balance(state)
                    returned_orders[key] = max(0, balance - before_orders)
            # Remove cancelled female crew before parking reward tanks. This
            # releases their barracks seats for other crew returned by the
            # same atomic reset; berth rewards are reversed only afterwards.
            revoked_crew = {}
            removed_crew = {}
            state.expire_recycled_tankmen(now)
            for key in list(snapshot.get('personalMissionTankwomen', {})):
                if requested.get(key, 0):
                    continue
                crew_key = 'crew:' + key
                if crew_key not in journal:
                    raise GarageError('PERSONAL_MISSION_CREW_PROVENANCE_MISSING: ' + key)
                removed_crew[key] = bool(personal_campaign_ledger.revoke_tankwoman(
                    state, journal[crew_key]))
                del journal[crew_key]
                snapshot['personalMissionTankwomen'].pop(key, None)
                bonus_key = 'crewBonus:' + key
                receipt = journal.get(bonus_key)
                if not isinstance(receipt, dict):
                    definition = _definition(key, definitions, open_section)
                    delayed = child(definition['main'], 'bonusDelayed')
                    bonus = {'children': [(name, row) for name, row in
                        (delayed or {}).get('children', ())
                        if name not in ('tankmen', 'dossier')]}
                    receipt = _legacy_reward(state, bonus, now)
                revoked_crew[key] = receipt
            # Remove dependent operation rewards before their source task
            # stages. Vehicle removal must precede withdrawing its garage slot.
            claimed = list(snapshot.get('personalMissionTokenRewards') or ())
            if claimed:
                eligible, quests = _operation_entitlements(requested, definitions, open_section)
                old_eligible, unused = _operation_entitlements(previous, definitions, open_section)
                ordered = old_eligible + [key for key in claimed if key not in old_eligible]
                for identifier in reversed(ordered):
                    if identifier not in claimed or identifier in eligible:
                        continue
                    receipt_key = 'operation:' + identifier
                    receipt = journal.get(receipt_key)
                    if not isinstance(receipt, dict):
                        if identifier not in quests:
                            raise GarageError('PERSONAL_MISSION_REWARD_SOURCE_UNAVAILABLE: ' + identifier)
                        receipt = _legacy_reward(state, child(quests[identifier], 'bonus'), now)
                    rows = _revoke_reward(state, receipt, now)
                    journal.pop(receipt_key, None)
                    claimed.remove(identifier)
                    revoked_operations.append({'id': identifier, 'phase': 'revoked', 'rewards': rows})
                snapshot['personalMissionTokenRewards'] = claimed
            for key in sorted(set(previous) | set(rewarded) | set(revoked_crew),
                              key=int, reverse=True):
                level = previous.get(key, 0)
                target = requested.get(key, 0)
                paid = rewarded.get(key, 0)
                if target >= level and target >= paid and key not in revoked_crew:
                    continue
                rows, orders_revoked, tankwomen_revoked = [], 0, 0
                if level == 2 and target < 2:
                    order_key = 'orders:' + key
                    if order_key in journal:
                        orders_revoked = personal_campaign_ledger.revoke_orders(
                            state, journal[order_key]['count'])
                        del journal[order_key]
                if key in revoked_crew:
                    tankwomen_revoked = int(removed_crew[key])
                    rows.extend(_revoke_reward(state, revoked_crew[key], now))
                    journal.pop('crewBonus:' + key, None)
                for stage in range(paid, target, -1):
                    stage_name = 'main' if stage == 1 else 'add'
                    receipt_key = 'stage:%s:%s' % (key, stage_name)
                    receipt = journal.get(receipt_key)
                    if not isinstance(receipt, dict):
                        definition = _definition(key, definitions, open_section)
                        receipt = _legacy_reward(state, child(definition[stage_name], 'bonus'), now)
                    rows.extend(_revoke_reward(state, receipt, now))
                    journal.pop(receipt_key, None)
                if paid > target:
                    if target:
                        rewarded[key] = target
                    else:
                        rewarded.pop(key, None)
                revoked_missions.append({'id': int(key), 'phase': 'revoked',
                    'before': level, 'after': target, 'paid_before': paid,
                    'paid_after': min(paid, target), 'paid_stages': [],
                    'rewards': rows, 'orders_revoked': orders_revoked,
                    'orders_refunded': returned_orders.get(key, 0),
                    'tankwomen_revoked': tankwomen_revoked,
                    'tankwomen_already_dismissed': int(key in removed_crew and
                                                     not removed_crew[key])})
            snapshot['personalMissionRewarded'] = rewarded
            snapshot['personalMissionProgress'] = requested
            if 'personalMissionRequestedRegular' in snapshot:
                snapshot['personalMissionSelections'] = {'regular':
                    data.personal_mission_regular_selection(snapshot['personalMissionRequestedRegular'])}
            snapshot.pop('personalMissionRequestedCompleted', None)
            snapshot.pop('personalMissionRequestedRegular', None)
            snapshot.pop('personalMissionResetError', None)
            state.revision += 1
    except Exception as error:
        # The transaction restored completion and every asset. Consume the
        # refused edit, so future logins do not repeat an impossible reset.
        snapshot = state.snapshot()
        snapshot.pop('personalMissionRequestedCompleted', None)
        snapshot.pop('personalMissionRequestedRegular', None)
        snapshot['personalMissionResetError'] = str(error)
        state.revision += 1
        return str(error)
    if notices is not None:
        notices['missions'].extend(revoked_missions)
        notices['operations'].extend(revoked_operations)
    return ''


def _earned_order_count(definition):
    return sum(int(value(row, 'count', '1')) for row in
               children(child(definition['add'], 'bonus'), 'token')
               if value(row, 'id') == 'free_award_list')


def order_balance(snapshot):
    """Available orders are earned final rewards less still-pledged orders.

    A legacy manually entered balance is not an entitlement. Excess legacy
    pledges remain attached to completed missions, but their later refund can
    only release real earned orders, never manufacture a positive balance.
    """
    progress = data.personal_mission_completed(snapshot.get('personalMissionProgress'))
    journal = snapshot.get('personalMissionRewardJournal') or {}
    earned = 0
    for mission_id in range(15, 301, 15):
        key = str(mission_id)
        if progress.get(key) == 2:
            row = journal.get('orders:' + key) or {}
            earned += max(0, int(row.get('count', 0)))
    pledged = sum(int(count) for count in
                  (snapshot.get('personalMissionPawned') or {}).values())
    return max(0, earned - pledged)


def _reconcile_order_balance(state):
    snapshot = state.snapshot()
    balance = order_balance(snapshot)
    if snapshot.get('personalMissionOrders', 0) != balance:
        snapshot['personalMissionOrders'] = balance
        state.revision += 1
    return balance


def _reconcile_order_sources(state, definitions=None, open_section=None):
    """Restore missing legacy receipts and validate counts against live XML."""
    snapshot = state.snapshot()
    progress = data.personal_mission_completed(snapshot.get('personalMissionProgress'))
    rewarded = data.personal_mission_completed(snapshot.get('personalMissionRewarded'))
    journal = dict(snapshot.get('personalMissionRewardJournal') or {})
    for key in list(journal):
        if key.startswith('orders:'):
            del journal[key]
    for mission_id in range(15, 301, 15):
        key = str(mission_id)
        if progress.get(key) != 2 or rewarded.get(key, 0) < 2:
            continue
        definition = (definitions[mission_id] if definitions is not None
                      else mission_definition(mission_id, open_section))
        count = _earned_order_count(definition)
        if count:
            journal['orders:' + key] = {'count': count}
    if journal != (snapshot.get('personalMissionRewardJournal') or {}):
        snapshot['personalMissionRewardJournal'] = journal
        state.revision += 1
    _reconcile_order_balance(state)


def settle(state, now=None, definitions=None, open_section=None):
    """Pay or reverse installed reward stages with durable exact receipts."""
    now = int(time.time() if now is None else now)
    granted, pending = [], []
    notices = {'missions': [], 'operations': []}
    initial_progress = data.personal_mission_completed(state.snapshot().get('personalMissionProgress'))
    initial_badges = set(data.account_badges(state.snapshot().get('accountBadges')))
    # Normalize before a reset checks its available refund budget. A save
    # without completed missions still needs to lose its old manual balance.
    _reconcile_order_balance(state)
    try:
        _reconcile_order_sources(state, definitions, open_section)
    except Exception as error:
        pending.append(('orders', str(error)))
    reset_error = _apply_requested_progress(state, now, definitions, open_section, notices)
    if reset_error:
        return {'completed': [], 'operation_rewards': [], 'missions': [],
                'operations': [], 'pending': pending, 'reset_error': reset_error}
    snapshot = state.snapshot()
    progress = data.personal_mission_completed(snapshot.get('personalMissionProgress'))
    rewarded = data.personal_mission_completed(snapshot.get('personalMissionRewarded'))
    for key in sorted(progress, key=int):
        level, paid = progress[key], rewarded.get(key, 0)
        order_key = 'orders:' + key
        journal = snapshot.setdefault('personalMissionRewardJournal', {})
        # Only final missions earn an order. Unchanged ordinary missions need
        # no resource read on every later battle settlement.
        order_may_be_due = level == 2 and int(key) % 15 == 0 and order_key not in journal
        pawn_may_be_due = level == 2 and key in snapshot.get('personalMissionPawned', {})
        if paid >= level and not order_may_be_due and not pawn_may_be_due:
            continue
        try:
            definition = _definition(key, definitions, open_section)
            paid_stages, rewards = [], []
            earned_delta, refund = 0, 0
            before_orders = order_balance(snapshot)
            with state._transaction():
                for stage in range(paid + 1, level + 1):
                    stage_name = 'main' if stage == 1 else 'add'
                    receipt_key = 'stage:%s:%s' % (key, stage_name)
                    if stage == 1 and key in snapshot.get('personalMissionPawned', {}):
                        stage_name = 'main_award_list'
                    receipt = _record_reward(state, child(definition[stage_name], 'bonus'), now)
                    journal[receipt_key] = receipt
                    paid_stages.append(stage)
                    rewards.extend(_reward_rows(receipt))
                earned_orders = (_earned_order_count(definition)
                                 if level == 2 and int(key) % 15 == 0 else 0)
                if earned_orders and order_key not in journal:
                    if paid >= 2:
                        # A prior reset retained the credits/items receipt but
                        # reclaimed this exact order reward. Restore only it.
                        snapshot['personalMissionOrders'] = data.personal_mission_orders(
                            snapshot.get('personalMissionOrders', 0) + earned_orders)
                    journal[order_key] = {'count': earned_orders}
                    earned_delta = earned_orders
                if level == 2:
                    refund = snapshot.setdefault('personalMissionPawned', {}).pop(key, 0)
                    snapshot['personalMissionOrders'] = data.personal_mission_orders(
                        snapshot.get('personalMissionOrders', 0) + int(refund))
                snapshot.setdefault('personalMissionRewarded', {})[key] = max(paid, level)
                current_orders = _reconcile_order_balance(state)
                refund = max(0, current_orders - before_orders - earned_delta)
                state.revision += 1
            granted.append(int(key))
            notices['missions'].append({'id': int(key), 'phase': 'granted',
                'before': initial_progress.get(key, 0), 'after': level,
                'paid_before': paid, 'paid_after': max(paid, level),
                'paid_stages': paid_stages, 'rewards': rewards,
                'orders_earned': earned_delta, 'orders_refunded': int(refund),
                'tankwoman_pending': bool(level and not snapshot.get(
                    'personalMissionTankwomen', {}).get(key) and
                    child(definition['main'], 'bonusDelayed'))})
        except Exception as error:
            snapshot = state.snapshot()
            pending.append((int(key), str(error)))
    progress_totals = None
    try:
        progress_totals = _rebuild_progress_tokens(state, progress, definitions, open_section)
        operation_rewards = []
        while True:
            batch = _token_quests(state, now, open_section, notices['operations'])
            operation_rewards.extend(batch)
            if not batch:
                break
    except Exception as error:
        pending.append(('operation', str(error)))
        operation_rewards = [row['id'] for row in notices['operations']
                             if row.get('phase') == 'granted']
    if progress_totals is not None:
        try:
            _reconcile_campaign_badges(state, progress_totals, now, open_section)
        except Exception as error:
            pending.append(('badges', str(error)))
    _reconcile_order_balance(state)
    final_badges = set(data.account_badges(state.snapshot().get('accountBadges')))
    for phase, changed in (('granted', final_badges - initial_badges),
                           ('revoked', initial_badges - final_badges)):
        if changed:
            notices['operations'].append({'id': 'badges', 'phase': phase,
                'rewards': [{'kind': 'badge', 'id': badge, 'count': 1}
                            for badge in sorted(changed)]})
    return {'completed': granted, 'operation_rewards': operation_rewards,
            'missions': notices['missions'], 'operations': notices['operations'],
            'pending': pending, 'reset_error': reset_error}


def claim_tankwoman(state, mission_id, nation, vehicle_id, role_id,
                    definition=None, now=None):
    """Redeem the native delayed crew choice, preserving its zero-skill data."""
    key = str(int(mission_id))
    snapshot = state.snapshot()
    if not snapshot.get('personalMissionProgress', {}).get(key):
        raise GarageError('PERSONAL_MISSION_NOT_COMPLETE')
    if snapshot.get('personalMissionTankwomen', {}).get(key):
        raise GarageError('PERSONAL_MISSION_REWARD_ALREADY_CLAIMED')
    definition = definition or mission_definition(int(mission_id))
    delayed = child(definition['main'], 'bonusDelayed')
    crew = children(child(delayed, 'tankmen'), 'tman')
    if len(crew) != 1:
        raise GarageError('PERSONAL_MISSION_HAS_NO_CREW_REWARD')
    tankmen = state._tankmen_module()
    role = state._crew_role_name(role_id)
    vehicle_cd = state._vehicles_module().makeIntCompactDescrByID('vehicle', int(nation), int(vehicle_id))
    vehicle_type = state._vehicles_module().getVehicleType(vehicle_cd)
    if not any(role in roles for roles in vehicle_type.crewRoles):
        raise GarageError('PERSONAL_MISSION_INVALID_CREW_ROLE')
    crew_data = {}
    for name, row in crew[0]['children']:
        raw = value(row)
        if name in ('isPremium', 'isFemale'):
            crew_data[name] = raw == 'true'
        elif name in ('skills', 'freeSkills'):
            crew_data[name] = raw.split()
        elif name == 'role':
            crew_data[name] = raw
        else:
            crew_data[name] = int(raw)
    crew_data.update(nationID=int(nation), vehicleTypeID=int(vehicle_id), role=role)
    descriptor = tankmen.makeTmanDescrByTmanData(crew_data)
    with state._transaction():
        tankman_id = state._next_tankman_id()
        state._to_barracks(tankman_id, descriptor)
        state._touched_tankmen.add(tankman_id)
        import base64
        journal = snapshot.setdefault('personalMissionRewardJournal', {})
        bonus_key = 'crewBonus:' + key
        bonus = {'children': [(name, row) for name, row in delayed['children']
                 if name != 'tankmen']}
        journal[bonus_key] = _record_reward(state, bonus,
            int(time.time() if now is None else now))
        journal['crew:' + key] = {'tankman': tankman_id,
            'descriptor': base64.b64encode(descriptor).decode('ascii'),
            # The economic receipt owns the dossier inverse for new claims.
            'dossier_count': 0}
        snapshot.setdefault('personalMissionTankwomen', {})[key] = True
        state.revision += 1
    return tankman_id
