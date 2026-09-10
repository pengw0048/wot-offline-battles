"""Measure GC-tracked objects and explicitly probe collectable cycles.

The regular census is read-only and does not call ``gc.collect()``.
``collect_once`` is a separate, explicit collection experiment at a lifecycle
boundary. Neither measurement covers all Python memory: untracked objects,
extension buffers, and native resources can grow without changing the census.
Growth or reclamation identifies paths to investigate, not a retaining owner
or proof of a leak. Reachable acyclic objects can accumulate too. Stable
counts cannot rule out growth in object size or untracked allocations.

It is diagnostics: every failure is swallowed and the caller continues.
"""

import gc
import itertools
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
# Bound both the object window and references inspected per source container.
# Only targets inside that window count. These partial, shape-grouped edges
# guide investigation; they do not identify complete cycles or retaining owners.
EDGE_SAMPLE = 2000
MAX_EDGE_REFERENTS = 32
TOP_EDGES = 8

# Finding an actual cycle, not just one edge of one.  `PYREF` reports pairs,
# which cannot close a loop; reports 20260910-045317/-061701/-065631 all show
# the dominant edge with no returning edge, because the cycle root is a small
# set of objects far from the head of gc.garbage.
#
# Two walks, because they fail in different directions.  The forward one finds
# a loop among plausible cycle members - anything that is not a plain
# container, so instances, bound methods, cells and frames.  The backward one
# walks referrers up from the shape that dominates the garbage and names what
# retains it, which is useful even when the cycle does not pass through it.
CYCLE_INDEX_LIMIT = 200000
CYCLE_SEEDS = 48
CYCLE_MAX_DEPTH = 24
CYCLE_NODE_BUDGET = 30000
TOP_CYCLES = 3
# One `gc.get_referrers` call scans every tracked object, so the depth here
# multiplies a whole-heap pass. Measured on CPython 2.7 at 303k tracked
# objects: 60 ms per level. The field worker carries about 1M, and Peng's VM
# emulates x86 on ARM64, so budget seconds rather than milliseconds. Four
# levels is enough to cross container -> owner -> registry -> holder, which is
# where a local reproduction of the observed shape gave the whole answer;
# deeper levels were module-level noise.
HOLD_SEEDS = 12
HOLD_DEPTH = 4
TOP_HOLDERS = 6
_PLAIN_CONTAINERS = (dict, list, tuple, set, frozenset)
TOP_SIGNATURES = 10
MAX_SIGNATURE_KEYS = 8
MAX_SIGNATURE_TEXT = 64
# Maximum dictionary entries visited for a key sample. Wider mappings report
# a partial sample, whose members can vary with the runtime's dictionary order.
KEY_SCAN_LIMIT = 64
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


def _signature(item):
    """Describe a bounded shape without invoking arbitrary object code."""
    item_type = type(item)
    kind = _type_name(item)
    try:
        # Exact types only: subclasses may override keys, length, or attribute
        # lookup and run application/native code while garbage is retained.
        if item_type is dict:
            # Include key names beyond the display limit while bounding the
            # scan itself. A sorted partial sample is not a stable mapping
            # identity: Python 2 dictionary order can change which keys fit.
            length = len(item)
            keys = []
            scanned = 0
            for key in itertools.islice(item, KEY_SCAN_LIMIT):
                scanned += 1
                if _is_text(key):
                    keys.append(_label(key))
            sampled = ('[sampled=%d/%d]' % (scanned, length)
                       if scanned < length else '')
            if not keys:
                return 'dict(len=%s)%s' % (_bucket(length), sampled)
            keys.sort()
            shown = keys[:MAX_SIGNATURE_KEYS]
            more = ''
            if length > len(shown):
                more = ',+%d' % (length - len(shown))
            return 'dict{%s%s}%s' % (','.join(shown), more, sampled)
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


def _edge_referents(item):
    """Visit bounded direct references of exact built-in containers only."""
    item_type = type(item)
    if item_type is dict:
        pairs = (item.iteritems() if sys.version_info[0] == 2 else
                 iter(item.items()))
        for key, value in itertools.islice(pairs, MAX_EDGE_REFERENTS // 2):
            yield key
            yield value
    elif any(item_type is builtin for builtin in (list, tuple, set, frozenset)):
        for target in itertools.islice(item, MAX_EDGE_REFERENTS):
            yield target


def edge_census(items, sample=EDGE_SAMPLE):
    """Summarize partial references between objects in the supplied window.

    The collector supplies unreachable objects, which can include acyclic
    contents held by cycles. An edge between two members does not prove both
    belong to a cycle, and grouping by shape loses individual object identity.
    Omitted targets, references beyond the per-container budget, subclasses,
    and opaque/native objects are not traversed. Missing edges prove nothing
    about those objects. Avoid gc.get_referents: it materializes every referent
    before a caller can limit the result and can invoke native tp_traverse.
    """
    window = list(itertools.islice(items, max(0, min(sample, EDGE_SAMPLE))))
    known = dict((id(item), _signature(item)) for item in window)
    tally = {}
    edges = 0
    for item in window:
        try:
            for target in _edge_referents(item):
                if id(target) not in known:
                    continue
                # Preserve repeated references and self-edges. Keep signatures
                # separate until rendering so large labels are not recopied
                # for every pair counted in the bounded window.
                key = (known[id(item)], known[id(target)])
                tally[key] = tally.get(key, 0) + 1
                edges += 1
        except Exception:
            continue
    ranked = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    return len(window), edges, [('%s -> %s' % pair, count)
                                for pair, count in ranked[:TOP_EDGES]]


def _walk_referents(item):
    """Referents for traversal, over every type a cycle can run through.

    ``_edge_referents`` is deliberately exact-type: it bounds fan-out for the
    edge histogram and yields nothing for an instance or a bound method, which
    are exactly the objects a cycle needs.  ``gc.get_referents`` is a C-level
    ``tp_traverse`` call - it runs no Python code, so it is as safe here - and
    it is the only way the walk can cross an instance.
    """
    try:
        return itertools.islice(gc.get_referents(item), MAX_EDGE_REFERENTS)
    except Exception:
        return iter(())


def _is_plain(item):
    item_type = type(item)
    return any(item_type is builtin for builtin in _PLAIN_CONTAINERS)


def cycle_census(items):
    """Return actual cycle paths found inside an unreachable set.

    Depth-first over referents restricted to the set; when a referent is
    already on the current path the loop is closed and rendered as
    ``A -> B -> C -> A``.  Seeds are the non-plain-container members, because
    a cycle needs something that can hold a back-reference and those are far
    fewer than the dicts they retain.
    """
    members = {}
    for item in itertools.islice(items, CYCLE_INDEX_LIMIT):
        members[id(item)] = item
    seeds = [item for item in members.values() if not _is_plain(item)]
    truncated = len(members) >= CYCLE_INDEX_LIMIT
    if not seeds:
        seeds = list(members.values())
    stride = max(1, len(seeds) // CYCLE_SEEDS)
    seeds = seeds[::stride][:CYCLE_SEEDS]
    budget = [CYCLE_NODE_BUDGET]
    found = []
    for seed in seeds:
        if budget[0] <= 0:
            break
        path = _find_cycle(seed, members, budget)
        if path and path not in found:
            found.append(path)
            if len(found) >= TOP_CYCLES:
                break
    return len(seeds), truncated, found


def _find_cycle(seed, members, budget):
    """Iterative DFS for one loop reachable from ``seed`` within the set."""
    stack = [(id(seed), iter(_walk_referents(seed)))]
    on_path = [id(seed)]
    marked = set(on_path)
    seen = set(on_path)
    while stack and budget[0] > 0:
        node_id, cursor = stack[-1]
        advanced = False
        for target in cursor:
            target_id = id(target)
            if target_id not in members:
                continue
            budget[0] -= 1
            if budget[0] <= 0:
                return None
            if target_id in marked:
                start = on_path.index(target_id)
                loop = [_signature(members[step])
                        for step in on_path[start:]]
                # Rotate to a canonical start so three reported cycles are
                # three different loops, not three entry points into one.
                pivot = loop.index(min(loop))
                loop = loop[pivot:] + loop[:pivot]
                return ' -> '.join(loop + [loop[0]])
            if target_id in seen or len(stack) >= CYCLE_MAX_DEPTH:
                continue
            seen.add(target_id)
            stack.append((target_id, iter(_walk_referents(target))))
            on_path.append(target_id)
            marked.add(target_id)
            advanced = True
            break
        if not advanced:
            stack.pop()
            marked.discard(on_path.pop())
    return None


def retention_census(items, own):
    """Walk referrers up from the dominant shape and name what retains it.

    ``own`` is the set of ids this probe itself holds, so the walk never
    reports its own bookkeeping as a retainer.
    """
    members = {}
    for item in itertools.islice(items, CYCLE_INDEX_LIMIT):
        members[id(item)] = item
    counts = {}
    for item in members.values():
        name = _signature(item)
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return '-', ()
    dominant = max(counts.items(), key=lambda pair: pair[1])[0]
    frontier = []
    for item in members.values():
        if len(frontier) >= HOLD_SEEDS:
            break
        if _signature(item) == dominant:
            frontier.append(item)
    if not frontier:
        return dominant, ()
    own = set(own)
    own.add(id(members))
    levels = []
    visited = set(id(item) for item in frontier)
    for unused_level in range(HOLD_DEPTH):
        # The index, work queue and call-argument tuple all retain the objects
        # being inspected. Name the tuple explicitly: CPython 2.7 reports the
        # temporary tuple created by ``*frontier`` as a referrer too.
        targets = tuple(frontier)
        own.update((id(frontier), id(targets)))
        try:
            referrers = gc.get_referrers(*targets)
        except Exception:
            break
        tally = {}
        following = []
        for holder in referrers:
            holder_id = id(holder)
            if (holder_id in own or holder_id in visited or
                    type(holder) is types.FrameType or
                    type(holder) is types.ModuleType):
                continue
            visited.add(holder_id)
            name = _signature(holder)
            tally[name] = tally.get(name, 0) + 1
            if len(following) < HOLD_SEEDS:
                following.append(holder)
        del referrers
        if not tally:
            break
        ranked = sorted(tally.items(), key=lambda pair: pair[1],
                        reverse=True)[:TOP_HOLDERS]
        levels.append(','.join('%s x%d' % pair for pair in ranked))
        if not following:
            break
        frontier = following
    return dominant, levels


def _collect_saved_sample(garbage, first):
    """Keep temporary object references and exception frames out of pass two."""
    try:
        unreachable = gc.collect()
        sample = garbage[first:first + SIGNATURE_SAMPLE]
        scanned, signatures = signature_census(sample)
        edge_window, edge_count, edges = edge_census(sample)
        # The whole unreachable set, not the head slice: the cycle root is a
        # handful of objects and the head is dominated by what it retains.
        whole = garbage[first:]
        seeds, truncated, cycles = cycle_census(whole)
        own = set((id(garbage), id(sample), id(whole), id(signatures),
                   id(edges), id(cycles), id(globals()), id(gc.__dict__)))
        dominant, holders = retention_census(whole, own)
        del whole
        return (unreachable, scanned, signatures, _by_type(sample),
                edge_window, edge_count, edges,
                seeds, truncated, cycles, dominant, holders)
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
        (unreachable, scanned, signatures, garbage_types,
         edge_window, edge_count, edges,
         seeds, seeds_truncated, cycles, dominant, holders) = sample
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
            'seeds': seeds,
            'seeds_truncated': 1 if seeds_truncated else 0,
            'cycles': cycles,
            'dominant': dominant,
            'holders': holders,
        }
    except Exception:
        return None


def format_collect_lines(phase, round_id, state=None):
    """Return the PYGC/PYSIG/PYREF group, or an empty tuple."""
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
    cycles = state.get('cycles') or ()
    loops = ('[Offline LAN 0.9.22] PYCYCLE phase=%s round=%s seeds=%d '
             'truncated=%d found=%d %s' % (
                 phase, round_id, state.get('seeds', 0),
                 state.get('seeds_truncated', 0), len(cycles),
                 ' || '.join(cycles) or 'none'))
    holders = state.get('holders') or ()
    chain = []
    for index, level in enumerate(holders):
        chain.append('L%d[%s]' % (index + 1, level))
    holds = ('[Offline LAN 0.9.22] PYHOLD phase=%s round=%s dominant=%s '
             'chain=%s' % (
                 phase, round_id, state.get('dominant', '-'),
                 ' <- '.join(chain) or 'none'))
    return (head, detail, held, loops, holds)


def log_collect(phase, round_id):
    """Run diagnostics and return whether a result was produced. Never raises.

    A logging failure does not require another collection: the diagnostic
    already completed its sampling and release passes.
    """
    try:
        lines = format_collect_lines(phase, round_id)
    except Exception:
        return False
    if not lines:
        return False
    try:
        for line in lines:
            sys.stdout.write(line + '\n')
    except Exception:
        pass
    return True
