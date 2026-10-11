"""Immutable campaign facts carried by the exactly-once battle receipt.

Native quest result cards consume the installed main/add unique quest IDs.
Notifications consume actual progress and paid-stage deltas, never the mutable
garage state at the time an old battle result is opened.
"""

import copy
import json


def before(snapshot):
    return dict((name, copy.deepcopy(snapshot.get(name) or {})) for name in (
        'personalMissionProgress', 'personalMissionRewarded',
        'personalMissionRewardJournal', 'personalMissionPawned'))


def collect(previous, snapshot, evaluations, settlement):
    from gui.mods.offline_lan_0922 import personal_campaign as campaign

    progress = snapshot.get('personalMissionProgress') or {}
    rewarded = snapshot.get('personalMissionRewarded') or {}
    old_progress = previous['personalMissionProgress']
    old_rewarded = previous['personalMissionRewarded']
    journal = snapshot.get('personalMissionRewardJournal') or {}
    old_journal = previous['personalMissionRewardJournal']
    pawned = snapshot.get('personalMissionPawned') or {}
    old_pawned = previous['personalMissionPawned']
    settled_rows = settlement.get('missions') or ()
    awarded_rows = dict((str(row['id']), row) for row in settled_rows
                       if row.get('phase') != 'revoked')
    identifiers = set(evaluations) | set(awarded_rows)
    identifiers.update(str(qid) for qid in settlement.get('completed', ()))
    result = [copy.deepcopy(row) for row in settled_rows
              if row.get('phase') == 'revoked']
    for key in sorted(identifiers, key=int):
        evaluated = evaluations.get(key) or {}
        definition = evaluated.get('definition')
        if not definition:
            try:
                definition = campaign.mission_definition(int(key))
            except Exception:
                # Reporting must not reject a durable reward when a resource
                # became unavailable after that reward was granted.
                definition = {}
        definition = definition or {}
        paid_before = int(old_rewarded.get(key, 0))
        paid_after = int(rewarded.get(key, 0))
        stages = list(range(paid_before + 1, paid_after + 1))
        bonuses = []
        for stage in stages:
            name = 'main' if stage == 1 else 'add'
            if stage == 1 and key in old_pawned:
                name = 'main_award_list'
            bonus = campaign.child(definition.get(name), 'bonus')
            if bonus is not None:
                bonuses.append(copy.deepcopy(bonus))
        order_key = 'orders:' + key
        earned = int((journal.get(order_key) or {}).get('count', 0))
        old_earned = int((old_journal.get(order_key) or {}).get('count', 0))
        row = {
            'id': int(key),
            'before': int(old_progress.get(key, 0)),
            'after': int(progress.get(key, 0)),
            'evaluated': key in evaluations,
            'main_quest': campaign.value(definition.get('main'), 'id'),
            'add_quest': campaign.value(definition.get('add'), 'id'),
            'main_complete': evaluated.get('main') is True,
            'add_complete': evaluated.get('add') is True,
            'paid_before': paid_before,
            'paid_after': paid_after,
            'paid_stages': stages,
            'rewards': bonuses,
            'orders_earned': max(0, earned - old_earned),
            # Only the settlement owner can distinguish a real refunded
            # order from removal of an invalid legacy pledge.
            'orders_refunded': 0,
            'tankwoman_pending': bool(progress.get(key) and paid_after and
                not (snapshot.get('personalMissionTankwomen') or {}).get(key) and
                campaign.child(definition.get('main'), 'bonusDelayed')),
        }
        paid_row = awarded_rows.get(key)
        if paid_row is not None:
            for field in ('phase', 'paid_before', 'paid_after', 'paid_stages',
                          'rewards', 'orders_earned', 'orders_refunded',
                          'tankwoman_pending'):
                if field in paid_row:
                    row[field] = copy.deepcopy(paid_row[field])
        result.append(row)
    # XML node children contain tuples in memory. Canonicalize now so a
    # replay after a JSON reload returns exactly the same receipt metadata.
    return json.loads(json.dumps(result))


def quests_progress(settlement):
    """Use the stock QuestsProgressBlock producer/consumer tuple contract.

    Its personal-mission branch reads a positive bonusCount delta as this battle's
    main/add completion. Zero deltas retain the active mission's condition card.
    Only missions actually evaluated for this battle may enter this mapping.
    """
    result = {}
    if not isinstance(settlement, dict):
        return result
    for row in settlement.get('missions', ()):
        if not row.get('evaluated'):
            continue
        for level, stage in enumerate(('main', 'add'), 1):
            identifier = row.get(stage + '_quest')
            if identifier:
                result[identifier] = (0,
                    {'bonusCount': int(row.get('before', 0) >= level)},
                    {'bonusCount': int(row.get('after', 0) >= level)})
    return result
