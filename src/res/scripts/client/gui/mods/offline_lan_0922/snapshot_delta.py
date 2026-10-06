"""Lossless actor deltas for one ordered snapshot transport.

Only players and bots depend on the previous wire snapshot. Every other
section keeps its existing per-message meaning. Baselines are private raw
state, never the mutable snapshots delivered to the runtime. Both peers use
this module on Python 2.7 or Python 3.
"""

import math


try:
    _INTEGER_TYPES = (int, long)
    _STRING_TYPES = (basestring,)
except NameError:
    _INTEGER_TYPES = (int,)
    _STRING_TYPES = (str,)

_ACTORS = ('players', 'bots')
_METADATA = ('snapshot_delta', 'snapshot_seq', 'snapshot_base_seq')
_LINEAGE = ('round_id', 'authority_epoch', 'map', 'bot_authority_id',
            'bot_manifest_revision')


class SnapshotDeltaError(ValueError):
    """A snapshot cannot be reconstructed against this transport baseline."""


_JSON_SCALARS = frozenset(
    _INTEGER_TYPES + (float, bool, type(None), str, type(u'')))


def _clone(value):
    """Detach plain JSON containers without generic object-copy dispatch."""
    kind = type(value)
    if kind in _JSON_SCALARS:
        return value
    if kind is dict:
        return {key: _clone(value[key]) for key in value}
    if kind is list or kind is tuple:
        return [_clone(item) for item in value]
    raise SnapshotDeltaError('unsupported snapshot JSON value')


class _Baseline(object):
    __slots__ = ('sequence', 'lineage', 'actors')

    def __init__(self, sequence, lineage, actors):
        self.sequence = sequence
        self.lineage = lineage
        self.actors = actors


def _integer(value):
    return isinstance(value, _INTEGER_TYPES) and not isinstance(value, bool)


def _sequence(value):
    if not _integer(value) or value <= 0:
        raise SnapshotDeltaError('invalid snapshot sequence')
    return value


def _lineage(message):
    for key in ('round_id', 'authority_epoch'):
        if not _integer(message.get(key)):
            raise SnapshotDeltaError('invalid snapshot lineage')
    authority = message.get('bot_authority_id')
    if ('bot_authority_id' not in message or
            (authority is not None and not _integer(authority))):
        raise SnapshotDeltaError('invalid snapshot authority')
    if not isinstance(message.get('map'), _STRING_TYPES):
        raise SnapshotDeltaError('invalid snapshot map')
    if ('bot_manifest_revision' in message and
            not _integer(message['bot_manifest_revision'])):
        raise SnapshotDeltaError('invalid snapshot manifest revision')
    return tuple(message.get(key) for key in _LINEAGE)


def _row(value):
    if (not isinstance(value, dict) or not _integer(value.get('id')) or
            any(not isinstance(key, _STRING_TYPES) for key in value)):
        raise SnapshotDeltaError('invalid snapshot actor row')
    return value['id']


def _rows(value):
    if not isinstance(value, list):
        raise SnapshotDeltaError('invalid snapshot actor list')
    indexed = {}
    for row in value:
        actor = _row(row)
        if actor in indexed:
            raise SnapshotDeltaError('duplicate snapshot actor')
        indexed[actor] = row
    return indexed


def _same(left, right):
    if left is right:
        return True
    # Python equates True, 1 and 1.0, but replacing one with another changes
    # the JSON value's type. Preserve that distinction without quantization.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _integer(left) or _integer(right):
        return _integer(left) and _integer(right) and left == right
    if isinstance(left, float) or isinstance(right, float):
        return (isinstance(left, float) and isinstance(right, float) and
                left == right and (left != 0.0 or
                    math.copysign(1.0, left) == math.copysign(1.0, right)))
    if isinstance(left, _STRING_TYPES) and isinstance(right, _STRING_TYPES):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return (set(left) == set(right) and
                all(_same(value, right[key]) for key, value in left.items()))
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return (len(left) == len(right) and
                all(_same(a, b) for a, b in zip(left, right)))
    return type(left) is type(right) and left == right


def _base(baseline):
    if baseline is not None and not isinstance(baseline, _Baseline):
        raise SnapshotDeltaError('invalid snapshot baseline')
    return baseline


def _message(message):
    if not isinstance(message, dict) or message.get('type') != 'snapshot':
        raise SnapshotDeltaError('invalid snapshot envelope')


def _other_fields(message):
    return _clone(dict((key, value) for key, value in message.items()
                       if key not in _ACTORS + _METADATA))


def _full(message, sequence, lineage):
    actors = {}
    for name in _ACTORS:
        _rows(message.get(name))
        actors[name] = _clone(message[name])
    return _Baseline(sequence, lineage, actors)


def _encode_rows(rows, previous):
    indexed = _rows(rows)
    old = dict((row['id'], row) for row in previous)
    removed = [row['id'] for row in previous if row['id'] not in indexed]
    added = [row for row in rows if row['id'] not in old]
    expected_order = ([row['id'] for row in previous if row['id'] in indexed] +
                      [row['id'] for row in added])
    if expected_order != [row['id'] for row in rows]:
        return None, None
    patch, updated, next_rows = {}, [], []
    if removed:
        patch['remove'] = removed
    if added:
        patch['add'] = _clone(added)
    for row in rows:
        actor = row['id']
        if actor not in old:
            next_rows.append(_clone(row))
            continue
        before = old[actor]
        changed = dict((key, value) for key, value in row.items()
                       if key != 'id' and
                       (key not in before or not _same(value, before[key])))
        deleted = sorted(key for key in before if key not in row)
        if not changed and not deleted:
            # Private baseline rows are immutable. Reuse unchanged values;
            # only changed fields need copies on a steady delta frame.
            next_rows.append(before)
            continue
        update = {'id': actor}
        after = dict(before)
        if changed:
            update['set'] = _clone(changed)
            after.update(_clone(changed))
        if deleted:
            update['remove'] = deleted
            for key in deleted:
                del after[key]
        updated.append(update)
        next_rows.append(after)
    if updated:
        patch['update'] = updated
    return patch, next_rows


def encode(message, baseline, sequence, force_full=False):
    """Encode at the writer boundary; commit next_baseline only after send."""
    _message(message)
    if any(key in message for key in _METADATA):
        raise SnapshotDeltaError('snapshot already has delta metadata')
    baseline = _base(baseline)
    sequence = _sequence(sequence)
    if baseline is not None and sequence <= baseline.sequence:
        raise SnapshotDeltaError('snapshot sequence did not advance')
    lineage = _lineage(message)
    full = bool(force_full or baseline is None or lineage != baseline.lineage)
    patches, actors = {}, {}
    if not full:
        for name in _ACTORS:
            patches[name], actors[name] = _encode_rows(
                message.get(name), baseline.actors[name])
            if patches[name] is None:
                full = True
                break
    wire = _other_fields(message)
    wire['snapshot_delta'] = not full
    wire['snapshot_seq'] = sequence
    if full:
        next_baseline = _full(message, sequence, lineage)
        for name in _ACTORS:
            wire[name] = _clone(next_baseline.actors[name])
    else:
        wire['snapshot_base_seq'] = baseline.sequence
        wire.update(patches)
        next_baseline = _Baseline(sequence, lineage, actors)
    return wire, next_baseline


def _apply_rows(patch, previous):
    if (not isinstance(patch, dict) or
            set(patch) - set(('add', 'update', 'remove'))):
        raise SnapshotDeltaError('invalid snapshot actor patch')
    added = patch.get('add', [])
    updates = patch.get('update', [])
    removed = patch.get('remove', [])
    additions = _rows(added)
    if not isinstance(updates, list) or not isinstance(removed, list):
        raise SnapshotDeltaError('invalid snapshot actor operations')
    indexed = dict((row['id'], row) for row in previous)
    touched = set()
    for actor in removed:
        if not _integer(actor) or actor in touched or actor not in indexed:
            raise SnapshotDeltaError('invalid snapshot actor removal')
        touched.add(actor)
    for actor in additions:
        if actor in touched or actor in indexed:
            raise SnapshotDeltaError('conflicting snapshot actor addition')
        touched.add(actor)
    result = dict((actor, row) for actor, row in indexed.items()
                  if actor not in removed)
    for update in updates:
        if (not isinstance(update, dict) or
                set(update) - set(('id', 'set', 'remove'))):
            raise SnapshotDeltaError('invalid snapshot actor update')
        actor = update.get('id')
        if not _integer(actor) or actor in touched or actor not in indexed:
            raise SnapshotDeltaError('conflicting snapshot actor update')
        touched.add(actor)
        changed = update.get('set', {})
        deleted = update.get('remove', [])
        if (not isinstance(changed, dict) or
                any(not isinstance(key, _STRING_TYPES) for key in changed) or
                'id' in changed or not isinstance(deleted, list) or
                any(not isinstance(key, _STRING_TYPES) for key in deleted) or
                len(set(deleted)) != len(deleted) or 'id' in deleted or
                set(deleted).intersection(changed)):
            raise SnapshotDeltaError('invalid snapshot field operations')
        after = dict(indexed[actor])
        for key in deleted:
            if key not in after:
                raise SnapshotDeltaError('missing snapshot field removal')
            del after[key]
        after.update(_clone(changed))
        result[actor] = after
    rows = [result[row['id']] for row in previous if row['id'] in result]
    rows.extend(_clone(added))
    return rows


def decode(wire, baseline):
    """Rebuild before receive coalescing; never mutate a supplied baseline."""
    _message(wire)
    baseline = _base(baseline)
    if not any(key in wire for key in _METADATA):
        # Existing complete-message consumers and recordings need no codec
        # state. An unsequenced snapshot cannot anchor a subsequent delta.
        return _clone(wire), None
    marker = wire.get('snapshot_delta')
    if not isinstance(marker, bool):
        raise SnapshotDeltaError('invalid snapshot delta marker')
    sequence = _sequence(wire.get('snapshot_seq'))
    if baseline is not None and sequence <= baseline.sequence:
        raise SnapshotDeltaError('snapshot sequence did not advance')
    lineage = _lineage(wire)
    if marker:
        base_sequence = _sequence(wire.get('snapshot_base_seq'))
        if (baseline is None or base_sequence != baseline.sequence or
                lineage != baseline.lineage):
            raise SnapshotDeltaError('snapshot delta baseline mismatch')
        actors = dict((name, _apply_rows(wire.get(name), baseline.actors[name]))
                      for name in _ACTORS)
        next_baseline = _Baseline(sequence, lineage, actors)
    else:
        if 'snapshot_base_seq' in wire:
            raise SnapshotDeltaError('full snapshot has a delta baseline')
        next_baseline = _full(wire, sequence, lineage)
    full = _other_fields(wire)
    for name in _ACTORS:
        full[name] = _clone(next_baseline.actors[name])
    return full, next_baseline
