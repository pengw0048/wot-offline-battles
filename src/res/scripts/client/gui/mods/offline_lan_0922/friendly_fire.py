"""Friendly-fire facts and descriptor-priced repair compensation.

The published guide charges the victim's repair cost plus ten percent and
pays the victim independently of the offender's funds. Only hull HP is priced,
matching the existing garage repair receipt; no module bill is fabricated.
https://wotgame.cn/zh-cn/content/guide/general/teamkill/
"""
import math

try:
    _INT_TYPES = (int, long)
    _TEXT_TYPES = (str, unicode)
except NameError:
    _INT_TYPES = (int,)
    _TEXT_TYPES = (str,)


def _integer(value, maximum=2147483647):
    if isinstance(value, bool) or not isinstance(value, _INT_TYPES) or not 0 <= value <= maximum:
        raise ValueError('invalid friendly-fire number')
    return int(value)


def facts(value=None):
    if value is None:
        return {'victims': [], 'received_damage': 0, 'xp_penalty': 0}
    if not isinstance(value, dict) or set(value) != {
            'victims', 'received_damage', 'xp_penalty'}:
        raise ValueError('invalid friendly-fire facts')
    if not isinstance(value['victims'], list) or len(value['victims']) > 30:
        raise ValueError('invalid friendly-fire victims')
    victims = []
    seen = set()
    for row in value['victims']:
        if not isinstance(row, dict) or set(row) != {
                'actor_kind', 'actor_id', 'vehicle', 'damage'}:
            raise ValueError('invalid friendly-fire victim')
        identity = (row['actor_kind'], _integer(row['actor_id']))
        name = row['vehicle']
        damage = _integer(row['damage'], 1000000)
        if (identity[0] not in ('player', 'bot') or not identity[1] or
                identity in seen or not isinstance(name, _TEXT_TYPES) or
                not 1 <= len(name) <= 96 or not damage):
            raise ValueError('invalid friendly-fire victim identity')
        victims.append(dict(row))
        seen.add(identity)
    return {'victims': victims,
            'received_damage': _integer(value['received_damage'], 1000000),
            'xp_penalty': _integer(value['xp_penalty'])}


def costs(value=None):
    if not value:
        return {}
    names = {'credits_penalty', 'credits_out', 'credits_in', 'gross_credits'}
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError('invalid friendly-fire settlement')
    return dict((name, _integer(value[name])) for name in names)


def price(value, own_vehicle, vehicles):
    value = facts(value)

    def repair(vehicle, damage):
        if not damage:
            return 0
        descriptor = vehicles.VehicleDescr(typeName=str(vehicle))
        per_point = float(descriptor.type.repairCost)
        if math.isnan(per_point) or math.isinf(per_point) or per_point < 0.0:
            raise ValueError('invalid native friendly-fire repair price')
        return _integer(int(math.floor(per_point * damage + 0.5)))

    outgoing = sum(repair(row['vehicle'], row['damage'])
                   for row in value['victims'])
    incoming = repair(own_vehicle, value['received_damage'])
    return {'credits_out': outgoing, 'credits_in': incoming,
            'credits_penalty': (outgoing + 5) // 10}


def settle(wallet, priced, gross_credits):
    """Pay compensation/fines before auto service, within available Credits.

    Victim compensation never depends on the offender's wallet. The offline
    wallet is nonnegative; exhaust available funds without inventing debt or
    changing gold/bonds. The result rows record the amount actually debited.
    """
    available = int(wallet['credits']) + priced['credits_in']
    outgoing = min(available, priced['credits_out'])
    available -= outgoing
    penalty = min(available, priced['credits_penalty'])
    wallet['credits'] = available - penalty
    return {'gross_credits': int(gross_credits), 'credits_out': outgoing,
            'credits_penalty': penalty, 'credits_in': priced['credits_in']}


def net_credits(settlement):
    return (settlement['gross_credits'] + settlement['credits_in'] -
            settlement['credits_out'] - settlement['credits_penalty'])
