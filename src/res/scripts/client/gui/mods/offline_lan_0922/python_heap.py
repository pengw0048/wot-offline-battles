"""Per-round census of objects tracked by Python's cyclic garbage collector.

This reports counts and common types, not total Python memory. Strings and
other untracked objects, extension buffers, and native resources retained by
Python references can consume memory without appearing in this census.
Private allocation sizes reported by MEMORY do not identify their allocator
either, so neither measurement can exclude Python as a source of pressure.

Growth across equivalent round boundaries identifies types to investigate.
It does not distinguish a leak from a cache or identify the retaining owner;
stable counts likewise cannot rule out growth in object size or untracked
allocations. Compare these trends with address-space and lifecycle evidence.

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
    # Release the temporary references held by the census before reporting.
    del tracked
    result = {
        'gc_tracked_objects': total,
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
        # Objects retained in gc.garbage need investigation. Their presence
        # alone does not identify a native owner or the cause of retention.
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
    return ('[Offline LAN 0.9.22] PYHEAP phase=%s round=%s gc_tracked_objects=%d '
            'modules=%d gc_counts=%s gc_thresholds=%s gc_enabled=%d '
            'gc_garbage=%d top=%s' % (
                phase, round_id, state['gc_tracked_objects'], state['modules'],
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
