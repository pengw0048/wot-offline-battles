"""Cumulative linear/angular momentum across the human/worker boundary."""
import math

MAX_ROWS = 30
MAX_SEQUENCE = 2147483647
MAX_TOTAL = 1e12


def actor_key(value):
    if not isinstance(value, type('')) and not isinstance(value, type(u'')):
        raise ValueError('invalid detached contact actor')
    parts = value.split(':')
    if (len(parts) != 2 or parts[0] not in ('bot', 'player') or
            not 1 <= int(parts[1]) <= MAX_SEQUENCE):
        raise ValueError('invalid detached contact actor')
    return '%s:%d' % (parts[0], int(parts[1]))


def normalize(rows):
    if not isinstance(rows, (tuple, list)) or len(rows) > MAX_ROWS:
        raise ValueError('invalid detached contact list')
    result = {}
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 8:
            raise ValueError('invalid detached contact checkpoint')
        key = actor_key(row[0])
        seq = row[1]
        if (isinstance(seq, bool) or int(seq) != seq or
                not 1 <= seq <= MAX_SEQUENCE or key in result):
            raise ValueError('invalid detached contact sequence')
        values = [float(v) for v in row[2:]]
        if any(isinstance(v, bool) for v in row[2:]) or any(
                math.isnan(v) or math.isinf(v) or abs(v) > MAX_TOTAL for v in values):
            raise ValueError('invalid detached contact momentum')
        result[key] = [key, int(seq)] + values
    return result


def record(ledger, key, linear, angular):
    values = tuple(linear) + tuple(angular)
    if not any(values):
        return
    old = ledger.get(key, [key, 0] + [0.0]*6)
    ledger[key] = [key, old[1]+1] + [a+b for a, b in zip(old[2:], values)]


def unseen(current, previous):
    if current is None or (previous is not None and current[1] <= previous[1]):
        return (0.0,)*6
    previous = previous or [None, 0] + [0.0]*6
    return tuple(a-b for a, b in zip(current[2:], previous[2:]))


def pending(ledger, key, acknowledgements, player_key):
    ack = next((r for r in acknowledgements if r[0] == player_key), None)
    return unseen(ledger.get(key), ack)
