"""Measure GC-tracked objects and explicitly probe collectable cycles.

The regular census is read-only and does not call ``gc.collect()``.
``collect_once`` is a separate, explicit collection experiment at a lifecycle
boundary. Neither measurement covers all Python memory: untracked objects,
extension buffers, and native resources can grow without changing the census.
Growth or reclamation identifies paths to investigate, not a retaining owner
or proof of a leak. Reachable acyclic objects can accumulate too.

It is diagnostics: every failure is swallowed and the caller continues.
"""

import gc
import sys
import types

try:
    _TEXT_TYPES = (str, unicode)
except NameError:
    _TEXT_TYPES = (str,)
_TYPE_NAME = type.__dict__['__name__']

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

# Maximum objects to fingerprint across all types, and signatures to report.
# Small dict key sets can distinguish candidate structures more precisely
# than a type name, without identifying which owner retained them.
SIGNATURE_SAMPLE = 4000
TOP_SIGNATURES = 10
MAX_SIGNATURE_KEYS = 8
MAX_SIGNATURE_TEXT = 64
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
            # Read the built-in type slot, bypassing metaclass descriptors.
            name = _TYPE_NAME.__get__(type(item))
        except Exception:
            name = '?'
        tally[name] = tally.get(name, 0) + 1
    ranked = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    return [(_label(name), count) for name, count in ranked[:TOP_TYPES]]


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
    """Describe a bounded shape without invoking arbitrary object code."""
    item_type = type(item)
    kind = _type_name(item)
    try:
        # Exact types only: subclasses may override keys, length, or attribute
        # lookup and run application/native code while garbage is retained.
        if item_type is dict:
            if len(item) > MAX_SIGNATURE_KEYS:
                return 'dict(len=%s)' % _bucket(len(item))
            keys = sorted(_label(key) for key in item if _is_text(key))
            if not keys:
                return 'dict(len=%d)' % len(item)
            return 'dict{%s}' % ','.join(keys)
        if any(item_type is builtin for builtin in (list, tuple, set, frozenset)):
            return '%s(len=%s)' % (kind, _bucket(len(item)))
        code = None
        if item_type is types.FunctionType:
            code = getattr(item, 'func_code', None) or item.__code__
        elif item_type is types.CodeType:
            code = item
        if code is not None:
            return '%s@%s:%d' % (kind,
                                 _leaf(code.co_filename),
                                 code.co_firstlineno)
        if item_type is types.MethodType:
            function = (item.im_func if sys.version_info[0] == 2 else
                        item.__func__)
            if type(function) is types.FunctionType:
                return '%s:%s' % (kind, _label(function.__name__))
            return kind
        if item_type is types.FrameType:
            return 'frame@%s:%d' % (_leaf(item.f_code.co_filename),
                                    item.f_lineno)
        return kind
    except Exception:
        return kind


def _type_name(item):
    try:
        return _label(_TYPE_NAME.__get__(type(item)))
    except Exception:
        return '?'


def _label(value):
    """Bound log text and keep one structural sample on one line."""
    if not _is_text(value):
        return '?'
    text = ''.join(str(char) if 32 <= ord(char) < 127 and
                   char not in ',{}|' else '_'
                   for char in value[:MAX_SIGNATURE_TEXT])
    return text + ('...' if len(value) > MAX_SIGNATURE_TEXT else '')


def _is_text(value):
    return any(type(value) is builtin for builtin in _TEXT_TYPES)


def _leaf(path):
    try:
        if not _is_text(path):
            return '?'
        text = _label(path[-MAX_SIGNATURE_TEXT:]).replace('\\', '/')
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


def _collect_saved_sample(garbage, first):
    """Keep temporary object references and exception frames out of pass two."""
    try:
        unreachable = gc.collect()
        sample = garbage[first:first + SIGNATURE_SAMPLE]
        scanned, signatures = signature_census(sample)
        return unreachable, scanned, signatures, _by_type(sample)
    except Exception:
        return None


def collect_once():
    """Sample unreachable objects and perform a second pass to release cycles.

    DEBUG_SAVEALL retains the first pass's objects long enough to fingerprint
    a bounded sample. Only this call's additions to gc.garbage are removed;
    an existing SAVEALL session is left untouched and this probe is skipped.
    Both normal and failed sampling restore the caller's debug flags and run
    the release pass. The automatic collector's enabled state is not changed.

    Reclaimed cycles are evidence of collectable retention, not proof of its
    cause or of native lifecycle safety. Live references can also accumulate.
    Exact Windows acceptance is still needed for native traversal and timing.
    """
    try:
        import time
        previous_debug = gc.get_debug()
        if previous_debug & gc.DEBUG_SAVEALL:
            return None
        garbage = gc.garbage
        if type(garbage) is not list:
            return None
        first = len(garbage)
        before = len(gc.get_objects())
        started = time.time()
        try:
            gc.set_debug(previous_debug | gc.DEBUG_SAVEALL)
            sample = _collect_saved_sample(garbage, first)
        finally:
            try:
                del garbage[first:]
            finally:
                gc.set_debug(previous_debug)
            # The sampling helper has returned, dropping references even if
            # it failed. Clearing SAVEALL's list alone cannot release cycles.
            freed_second = gc.collect()
        if sample is None:
            return None
        unreachable, scanned, signatures, garbage_types = sample
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
    return (head, detail)


def log_collect(phase, round_id):
    """Write the PYGC/PYSIG pair. Never raises into a caller."""
    try:
        for line in format_collect_lines(phase, round_id):
            sys.stdout.write(line + '\n')
    except Exception:
        pass
