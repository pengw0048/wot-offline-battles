"""Run one manual cyclic collection at the existing round teardown boundary.

The automatic collector can remain disabled while a manual collection releases
unreachable cycles and the objects they retain. This bounds that source of
retention; it does not identify the cycle's creator or account for all process
memory. The returned count is unreachable objects, not cycles or freed bytes.

The manual collection still traverses all GC generations. The sweep omits the
additional heap census and never enables automatic GC. An existing DEBUG_SAVEALL
session owns its retained garbage, so the sweep leaves it alone. Native traversal
safety and pause time still require exact Windows evidence.

One demonstrated creator is CPython 2.7's pure-Python JSON encoder. An exact
#1513 experiment found 34 garbage objects requiring cyclic collection after
one forced pure-Python encoding. Its mutually recursive closures become
unreachable, but successful encoding does not retain the whole input record.
Removing the forcing options from hot-path log calls addresses that producer;
it does not establish the cause of all observed process-memory growth. See
``tests/test_port_0922_json_encoder_cycles.py`` for the encoder checks.

The sweep remains for other unreachable cycles, including those created by
ordinary cyclic structures in mod or stock code. With automatic collection
disabled, such cycles remain until a manual collection can reclaim them.
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
        # The collection still scans all generations; using its result avoids
        # an additional heap census. This is the number of objects found
        # unreachable now, not cycles created this round or bytes released.
        # A change can guide investigation but does not identify its creator.
        # gc.garbage may also contain entries retained before this sweep, so
        # its length is not a per-round delta.
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
