"""Count selected engine registries at equivalent round boundaries.

Growth can help investigate entities or spaces retained across rounds, but a
registry count does not identify an object's age, retaining owner, memory
cost, or whether retention is expected. A count alone cannot prove a leak.
Native resources can also remain after an entity leaves these registries, so
stable counts cannot rule out a leak or attribute memory to Python or C++.
Use these trends alongside PYHEAP, MEMORY, and lifecycle evidence.

Only reads whose shape the stock #1513 client itself relies on are treated as
known: `BigWorld.entities` is indexed with `.get`/`.keys()` in `vehicle.py`,
`TriggersManager.py` and `Account.py`, and `BigWorld.userDataObjects` with
`.values()` in `ClientHangarSpace.py` and `MapCaseMode.py`.  Everything else
is attempted and reported as unavailable rather than assumed.

It is diagnostics: every failure is swallowed and the caller continues.
"""

import sys

# Registries to count, in report order.  `spaces` is listed because this port
# calls `addSpaceGeometryMapping` once per round and a space that is never
# released would retain the whole map's geometry - but no stock script reads
# `BigWorld.spaces`, so its shape is unproven and a failure to count it is
# reported rather than guessed at.
REGISTRIES = ('entities', 'userDataObjects', 'spaces')


def _bigworld():
    try:
        import BigWorld
        return BigWorld
    except ImportError:
        return None


def snapshot():
    """Return {registry: count}, with -1 where it could not be counted."""
    bigworld = _bigworld()
    if bigworld is None:
        return None
    counts = {}
    for name in REGISTRIES:
        counts[name] = _count(getattr(bigworld, name, None))
    if all(value < 0 for value in counts.values()):
        return None
    return counts


def _count(registry):
    if registry is None:
        return -1
    try:
        return len(registry)
    except (TypeError, AttributeError, ValueError):
        return -1


def format_line(phase, round_id, state=None):
    """Return the one WORLD line for this boundary, or None."""
    state = snapshot() if state is None else state
    if not state:
        return None
    parts = []
    for name in REGISTRIES:
        value = state.get(name, -1)
        parts.append('%s=%s' % (name, 'n/a' if value < 0 else value))
    return ('[Offline LAN 0.9.22] WORLD phase=%s round=%s %s'
            % (phase, round_id, ' '.join(parts)))


def log(phase, round_id):
    """Write one WORLD line. Never raises into a caller."""
    try:
        line = format_line(phase, round_id)
        if line is not None:
            sys.stdout.write(line + '\n')
    except Exception:
        pass
