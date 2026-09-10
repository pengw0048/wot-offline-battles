"""Collect the reference cycles this runtime would otherwise never free.

This is a fix, not instrumentation - it is kept apart from ``python_heap`` for
that reason.

BigWorld disables CPython's cyclic collector at startup: native code imports
``gc`` and calls ``disable`` (``0x0068F4FA`` / ``0x0068F54A`` in #1513), and
both roles report ``gc_enabled=0``.  Reference counting still frees everything
acyclic the moment it goes out of scope, so the process runs fine - but any
reference cycle becomes permanent.  In this runtime a cycle is not merely
untidy, it is a leak.

Measured in report `20260910-045317`, per round in the hidden worker:

    round 1  unreachable=107602   round 2  unreachable=348581
    round 3  unreachable=333240

against 4832 and 3010 in the visible client on the same machine over the same
rounds.  The worker's simulation path creates roughly a hundred times more
cycles per round than the client's, and the previous session without a collect
grew 1.14M -> 2.87M tracked objects over six rounds (~+347k per round, linear)
while the client stayed flat.  With one collect per round the worker's
round_start census is flat instead: 1133959 -> 1033952 -> 1044656.

The root cause - which cycle, in which subsystem - is still unidentified.  A
local harness drives 200 `BotRuntime.update` calls with Bots and a human
player and produces **zero** cycles, so it is not the Bot update path; see
`tests/test_port_0922_gc_sweep.py`.  Until it is found this sweep is what
keeps the address space bounded, and it is worth keeping afterwards: the next
cycle anyone writes would otherwise leak silently.

Deliberately minimal.  One ``gc.collect()``, no ``DEBUG_SAVEALL``, no object
census, no second pass - ``python_heap.collect_once`` does all of that and its
reported cost is not this one's.  It never enables the collector, because
leaving it enabled would change the engine's own choice for every frame rather
than once per round.
"""

import gc
import sys


def sweep(phase, round_id):
    """Collect cycles once and report it. Returns the count, or -1.

    Never raises into the caller: a boundary that cannot collect is worth a
    log line, not a lost round.
    """
    try:
        import time
        started = time.time()
        collected = gc.collect()
        elapsed_ms = int((time.time() - started) * 1000.0)
    except Exception:
        return -1
    try:
        sys.stdout.write(
            '[Offline LAN 0.9.22] PYSWEEP phase=%s round=%s collected=%d '
            'elapsed_ms=%d gc_enabled=%d\n' % (
                phase, round_id, collected, elapsed_ms,
                1 if gc.isenabled() else 0))
    except Exception:
        pass
    return collected
