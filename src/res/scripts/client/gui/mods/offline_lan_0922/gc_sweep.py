"""Run one manual cyclic collection at the existing round teardown boundary.

The automatic collector can remain disabled while a manual collection releases
unreachable cycles and the objects they retain. This bounds that source of
retention; it does not identify the cycle's creator or account for all process
memory. The returned count is unreachable objects, not cycles or freed bytes.

The sweep takes no heap census and never enables automatic GC. An existing
DEBUG_SAVEALL session owns its retained garbage, so the sweep leaves it alone.
Native traversal safety and pause time still require exact Windows evidence.

One creator is now known and fixed. Report 20260910-072722 closed the loop as
``cell -> function@encoder.py:288 -> tuple -> cell``, which is
``json.encoder._make_iterencode``: CPython 2.7's pure-Python encoder, whose
``_iterencode``/``_iterencode_dict``/``_iterencode_list`` are mutually
recursive closures, selected whenever ``indent`` is set or ``sort_keys`` is
true. Hot-path log lines passed ``sort_keys=True``; see
``tests/test_port_0922_json_encoder_cycles.py``.

The sweep stays regardless. That was one creator, and the remaining ones are
not all ours: every sampled round also carried ``OrderedDict``, ``_Link`` and
``weakproxy`` in fixed counts, and py2.7's ``OrderedDict`` keeps a
self-referencing linked root. With the automatic collector disabled, any
ordinary cycle - ours or stock - is permanent.
"""

import gc
import sys


def _write(line):
    try:
        sys.stdout.write(line + '\n')
    except Exception:
        pass


def sweep(phase, round_id):
    """Return the unreachable-object count, or -1 when skipped or failed."""
    prefix = '[Offline LAN 0.9.22] PYSWEEP phase=%s round=%s' % (phase, round_id)
    try:
        import time
        if gc.get_debug() & gc.DEBUG_SAVEALL:
            _write(prefix + ' skipped=debug_saveall')
            return -1
        started = time.time()
        unreachable = gc.collect()
        elapsed_ms = int((time.time() - started) * 1000.0)
        # `unreachable` is the regression signal the retired census used to
        # provide, and a cheaper one: it counts what this round left in cycles
        # rather than walking the whole heap. A step change means new cyclic
        # retention. `uncollectable` should stay zero - anything parked in
        # gc.garbage is a cycle the collector gave up on.
        try:
            uncollectable = len(gc.garbage)
        except Exception:
            uncollectable = -1
        _write('%s unreachable=%d uncollectable=%d elapsed_ms=%d '
               'gc_enabled=%d' % (
                   prefix, unreachable, uncollectable, elapsed_ms,
                   1 if gc.isenabled() else 0))
        return unreachable
    except Exception:
        _write(prefix + ' error=collection_failed')
        return -1
