"""Round-local cumulative physical responses, independent of armour/HP receipts.

Each visible human reports the opposite momentum of its contact impulse.
The worker divides the unseen momentum by its canonical Bot mass.
Cumulative checkpoints survive input/snapshot coalescing; retries are no-ops.
The acknowledgement travels with the Bot velocity it produced, so prediction
subtracts exactly the already-integrated share. The two position fields are
retained but no longer generate or predict displacement: only an owner's
accepted pose can clear occupied space. Acknowledgement means consumed input,
not proof that static geometry permitted a positional request.
"""
import math

MAX_ACTORS = 30
MAX_SEQUENCE = 2147483647
MAX_TOTAL = 1000000000000.0


def normalize(rows):
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ACTORS:
        raise ValueError('invalid contact checkpoint list')
    result = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 7:
            raise ValueError('invalid contact checkpoint')
        actor, seq, x, z, separation_x, separation_z, angular = row
        if (isinstance(actor, bool) or isinstance(seq, bool) or
                int(actor) != actor or int(seq) != seq or
                not 1 <= actor <= MAX_SEQUENCE or
                not 1 <= seq <= MAX_SEQUENCE or actor in result):
            raise ValueError('invalid contact identity')
        values = []
        for value in (x, z, separation_x, separation_z, angular):
            if isinstance(value, bool):
                raise ValueError('invalid contact total')
            number = float(value)
            if math.isnan(number) or math.isinf(number) or abs(number) > MAX_TOTAL:
                raise ValueError('invalid contact total')
            values.append(number)
        result[int(actor)] = [int(actor), int(seq)] + values
    return result


def record(ledger, actor, delta, separation=(0.0, 0.0), angular=0.0):
    if not any(delta) and not any(separation) and not angular:
        return
    old = ledger.get(actor, [actor, 0, 0.0, 0.0, 0.0, 0.0, 0.0])
    ledger[actor] = [actor, old[1] + 1,
                     old[2] + delta[0], old[3] + delta[1],
                     old[4] + separation[0], old[5] + separation[1], old[6] + angular]


def unseen(row, previous, separation=False):
    index = 4 if separation else 2
    if previous is None:
        return row[index], row[index+1]
    if row[1] <= previous[1]:
        return 0.0, 0.0
    return row[index] - previous[index], row[index+1] - previous[index+1]


def pending(ledger, actor, acknowledgements, player_id, separation=False):
    row = ledger.get(actor)
    if row is None:
        return 0.0, 0.0
    acknowledged = next((r for r in acknowledgements or ()
                         if r[0] == player_id), None)
    return unseen(row, acknowledged, separation=separation)


def unseen_angular(row, previous):
    if previous is None:
        return row[6]
    return row[6]-previous[6] if row[1] > previous[1] else 0.0


def pending_angular(ledger, actor, acknowledgements, player_id):
    row = ledger.get(actor)
    if row is None:
        return 0.0
    previous = next((r for r in acknowledgements or () if r[0] == player_id), None)
    return unseen_angular(row, previous)
