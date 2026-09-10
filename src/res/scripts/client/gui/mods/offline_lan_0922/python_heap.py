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
# Deliberately left where it is: report 20260910-034953 crossed it on round 4,
# so `top=` is blank from there on, but rounds 1-3 already establish the shape
# and the per-round growth is linear.  Repeating the same histogram for later
# rounds would buy nothing, and the signature census below answers the
# question the histogram cannot.
MAX_CENSUS_OBJECTS = 2000000

# How many objects of one type to fingerprint, and how many fingerprints to
# report.  A dict's key set identifies the structure that leaked far more
# precisely than the word "dict": report 20260910-034953 grew ~121k dicts,
# ~117k lists and ~112k tuples per round in the worker while every named
# class stayed flat, so the type histogram alone cannot say what they are.
SIGNATURE_SAMPLE = 4000
# Edges reported between two objects that are BOTH unreachable.  The shape
# census says what is in the leaked graph; only an edge inside that graph says
# what holds it together, which is the difference between "dicts leaked" and
# "this structure is the cycle".  Stock #1513's own
# GarbageCollectionDebug.get_refs builds the same source/target edges.
EDGE_SAMPLE = 2000
TOP_EDGES = 8
TOP_SIGNATURES = 10
MAX_SIGNATURE_KEYS = 8
# List and tuple lengths are reported as exact small values and then in
# powers-of-two buckets, so one dominant shape is still visible.
SMALL_LENGTH = 8


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


def _signature(item):
    """Fingerprint one object by what it structurally is.

    "dict" is not an answer.  A dict's key set names the record that leaked;
    a list's or tuple's length separates a 3-vector from a per-frame row; a
    function or code object gives a file and a line outright.
    """
    kind = type(item).__name__
    try:
        if isinstance(item, dict):
            keys = sorted(str(key) for key in item.keys()
                          if isinstance(key, str))
            if not keys:
                return 'dict(len=%d)' % len(item)
            shown = keys[:MAX_SIGNATURE_KEYS]
            more = '+%d' % (len(keys) - len(shown)) if len(keys) > len(
                shown) else ''
            return 'dict{%s%s}' % (','.join(shown), more)
        if isinstance(item, (list, tuple, set, frozenset)):
            return '%s(len=%s)' % (kind, _bucket(len(item)))
        code = getattr(item, 'func_code', None) or getattr(
            item, '__code__', None)
        if code is None and kind == 'code':
            code = item
        if code is not None:
            return '%s@%s:%d' % (kind,
                                 _leaf(code.co_filename),
                                 code.co_firstlineno)
        if kind == 'instancemethod':
            return 'instancemethod:%s' % getattr(item, '__name__', '?')
        if kind == 'frame':
            return 'frame@%s:%d' % (_leaf(item.f_code.co_filename),
                                    item.f_lineno)
        return kind
    except Exception:
        return kind


def _leaf(path):
    try:
        text = str(path).replace('\\', '/')
        return text.rsplit('/', 1)[-1]
    except Exception:
        return '?'


def _bucket(length):
    """Exact for short lengths, powers of two above, so a shape stands out."""
    if length <= SMALL_LENGTH:
        return str(length)
    edge = SMALL_LENGTH
    while edge < length:
        edge *= 2
    return '<=%d' % edge


def signature_census(items, limit=SIGNATURE_SAMPLE):
    """Return the most common structural fingerprints in ``items``."""
    tally = {}
    scanned = 0
    for item in items:
        if scanned >= limit:
            break
        scanned += 1
        name = _signature(item)
        tally[name] = tally.get(name, 0) + 1
    ranked = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    return scanned, ranked[:TOP_SIGNATURES]


def edge_census(items, sample=EDGE_SAMPLE):
    """Return the most common reference edges *within* an unreachable set.

    An object whose referents include another member of the same unreachable
    set is part of what keeps that set alive.  Reporting those pairs by
    signature names the cycle - `dict{...} -> list(len=1)` is a diagnosis,
    where `dict` on its own is not.
    """
    try:
        window = items[:sample]
    except TypeError:
        window = list(items)[:sample]
    known = {}
    for item in window:
        known[id(item)] = item
    tally = {}
    edges = 0
    for item in window:
        try:
            referents = gc.get_referents(item)
        except Exception:
            continue
        source = None
        for target in referents:
            if id(target) not in known or target is item:
                continue
            if source is None:
                source = _signature(item)
            key = '%s -> %s' % (source, _signature(target))
            tally[key] = tally.get(key, 0) + 1
            edges += 1
    ranked = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    return len(window), edges, ranked[:TOP_EDGES]


def collect_once():
    """Force one collection and report what it found. Never raises.

    This is the experiment that separates the two explanations for the
    worker's growth in report 20260910-034953 - 1.14M tracked objects on
    round 1 to 2.87M on round 6, while the visible client on the same machine
    stayed flat around 1.19M:

    * unreachable cycles that nobody collects, because the engine disables
      the cyclic collector at startup (`gc` is imported and `disable` called
      from native code at ``0x0068F4FA``/``0x0068F54A``, and both roles report
      ``gc_enabled=0``).  Reference counting frees everything acyclic, so only
      cycles can accumulate - and a periodic collect would then be a real fix.
    * objects something still references, in which case a collect frees
      nothing and the holder has to be found.

    ``DEBUG_SAVEALL`` makes the collector put what it found in ``gc.garbage``
    instead of freeing it, which is how stock #1513's own
    ``GarbageCollectionDebug.gcDump`` inspects a leak.  We fingerprint a
    bounded sample, drop the list, and collect again to actually free it -
    members of a cycle keep each other alive, so clearing the list is not
    enough on its own.

    **This deliberately does something the engine chose not to do.** BigWorld
    disables the cyclic collector, and a traversal of native objects is
    exactly what that decision avoids, so a collect here could be slow or
    could fault in a native ``tp_traverse``.  It runs at a round boundary,
    once, and reports its own elapsed time so the cost is visible.  It never
    re-enables the collector.
    """
    try:
        import time
        before = len(gc.get_objects())
        started = time.time()
        previous_debug = gc.get_debug()
        gc.set_debug(gc.DEBUG_SAVEALL)
        try:
            unreachable = gc.collect()
            scanned, signatures = signature_census(gc.garbage)
            garbage_types = _by_type(gc.garbage[:SIGNATURE_SAMPLE])
            edge_window, edge_count, edges = edge_census(gc.garbage)
        finally:
            del gc.garbage[:]
            gc.set_debug(previous_debug)
        # The cycle members still point at each other, so refcounting cannot
        # free them; this second pass is what actually reclaims the memory.
        freed_second = gc.collect()
        elapsed_ms = int((time.time() - started) * 1000.0)
        after = len(gc.get_objects())
        return {
            'before': before,
            'after': after,
            'unreachable': unreachable,
            'second_pass': freed_second,
            'elapsed_ms': elapsed_ms,
            'enabled_after': 1 if gc.isenabled() else 0,
            'scanned': scanned,
            'signatures': signatures,
            'types': garbage_types,
            'edge_window': edge_window,
            'edge_count': edge_count,
            'edges': edges,
        }
    except Exception:
        return None


def format_collect_lines(phase, round_id, state=None):
    """Return the PYGC line and its PYSIG companion, or an empty tuple."""
    state = collect_once() if state is None else state
    if not state:
        return ()
    head = ('[Offline LAN 0.9.22] PYGC phase=%s round=%s before=%d '
            'unreachable=%d after=%d net_freed=%d second_pass=%d '
            'elapsed_ms=%d gc_enabled_after=%d' % (
                phase, round_id, state['before'], state['unreachable'],
                state['after'], state['before'] - state['after'],
                state['second_pass'], state['elapsed_ms'],
                state['enabled_after']))
    types = state.get('types') or ()
    signatures = state.get('signatures') or ()
    detail = ('[Offline LAN 0.9.22] PYSIG phase=%s round=%s sampled=%d '
              'types=%s shapes=%s' % (
                  phase, round_id, state.get('scanned', 0),
                  ','.join('%s:%d' % pair for pair in types) or '-',
                  ' | '.join('%s x%d' % pair for pair in signatures) or '-'))
    edges = state.get('edges') or ()
    held = ('[Offline LAN 0.9.22] PYREF phase=%s round=%s window=%d edges=%d '
            'holds=%s' % (
                phase, round_id, state.get('edge_window', 0),
                state.get('edge_count', 0),
                ' | '.join('%s x%d' % pair for pair in edges) or '-'))
    return (head, detail, held)


# The diagnostic collect costs two full collections and two full
# `gc.get_objects()` passes on top of the one collection the fix needs, so its
# reported `elapsed_ms` is NOT the cost of `gc_sweep`.  Set this to False to
# leave only the fix in place and measure that cost on its own.
DIAGNOSTIC_COLLECT = True


def log_collect(phase, round_id):
    """Write the PYGC/PYSIG/PYREF group. Never raises into a caller."""
    if not DIAGNOSTIC_COLLECT:
        return
    try:
        for line in format_collect_lines(phase, round_id):
            sys.stdout.write(line + '\n')
    except Exception:
        pass
