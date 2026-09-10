"""Count what the engine still holds from previous rounds.

Peng's case (2026-09-10): our code makes the game create something each round
and never cleans it up.  The memory is BigWorld's, on the C++ heap, so neither
`PYHEAP` nor `MEMORY`'s arena count would attribute it to us - but the cause
is ours, and a leaked entity is not a small thing.  One Vehicle drags its
compound model, appearance, track spline, sound sources and particle systems
with it.

That case splits in two:

* Python still references the object, directly or through a live callback or
  closure.  `PYHEAP`'s type histogram already catches this and names the type.
* Python dropped it but the native resource was never released.  Nothing in
  Python can see that one, and it is the case this module exists for.

So count the stock registries instead.  At a round boundary in the lobby these
should return to roughly where they started; a count that climbs with the
round number is last round's world still resident, which is exactly the shape
Peng described.

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
