"""Per-round census of the Python heap, to settle leak-versus-cache.

The two 2026-09-09/10 worker crashes died with 3396 MiB and 3057 MiB of
private commit, of which CPython 2.7's obmalloc arenas were 21.0 MiB and
24.8 MiB - about 0.6%.  That proves Python was not what exhausted the address
space.  It does **not** prove Python was not leaking: a heap that grew 5 MiB
to 25 MiB across seven rounds has quintupled, which is a real leak, and one
dump taken at the moment of death cannot show a rate at all.

So this counts the heap itself, once per round boundary, and names the types
holding the most objects.  A type whose count climbs linearly with the round
number is a leak with an owner; a heap that returns to its previous level
after each round is a cache doing its job.  Either answer is worth having, and
neither needs a dump.

Everything here is read-only.  In particular it never calls ``gc.collect()``:
the question is what the process is actually holding during play, and
collecting first would measure something the game never experiences.

It is diagnostics: every failure is swallowed and the caller continues.
"""

import gc
import sys

# How many type names to report.  Enough to cover the port's own containers
# plus the stock client's entity and GUI objects, short enough for one line.
TOP_TYPES = 12
# Guard against a pathological heap making a round boundary visibly stall.
# The two crashed workers are the shape this is sized for; a census beyond
# this many objects reports the count and skips the per-type histogram.
MAX_CENSUS_OBJECTS = 2000000


def snapshot():
    """Return the heap census, or None when it cannot be taken."""
    try:
        return _census()
    except Exception:
        return None


def _census():
    counts = gc.get_count()
    tracked = gc.get_objects()
    total = len(tracked)
    types = None
    if total <= MAX_CENSUS_OBJECTS:
        types = _by_type(tracked)
    # Drop the census list's own reference before reporting, so the number
    # reported is not inflated by the act of measuring.
    del tracked
    result = {
        'objects': total,
        'gc_counts': tuple(counts),
        'types': types,
        'modules': 0,
        'garbage': 0,
        'thresholds': tuple(gc.get_threshold()),
        'enabled': 1 if gc.isenabled() else 0,
    }
    try:
        result['modules'] = len(sys.modules)
    except Exception:
        pass
    try:
        # Uncollectable objects gc gave up on. Non-zero here is its own bug,
        # and it is exactly the shape a reference cycle through a native
        # object takes.
        result['garbage'] = len(gc.garbage)
    except Exception:
        pass
    return result


def _by_type(tracked):
    """Count tracked objects by type name, most numerous first."""
    tally = {}
    for item in tracked:
        try:
            name = type(item).__name__
        except Exception:
            name = '?'
        tally[name] = tally.get(name, 0) + 1
    ranked = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    return ranked[:TOP_TYPES]


def format_line(phase, round_id, state=None):
    """Return the one PYHEAP line for this boundary, or None."""
    state = snapshot() if state is None else state
    if not state:
        return None
    types = state.get('types')
    rendered = ('-' if not types else
                ','.join('%s:%d' % (name, count) for name, count in types))
    return ('[Offline LAN 0.9.22] PYHEAP phase=%s round=%s objects=%d '
            'modules=%d gc_counts=%s gc_thresholds=%s gc_enabled=%d '
            'gc_garbage=%d top=%s' % (
                phase, round_id, state['objects'], state['modules'],
                '/'.join(str(value) for value in state['gc_counts']),
                '/'.join(str(value) for value in state['thresholds']),
                state['enabled'], state['garbage'], rendered))


def log(phase, round_id):
    """Write one PYHEAP line. Never raises into a caller."""
    try:
        line = format_line(phase, round_id)
        if line is not None:
            sys.stdout.write(line + '\n')
    except Exception:
        pass
