"""Run one manual cyclic collection at the existing round teardown boundary.

The automatic collector can remain disabled while a manual collection releases
unreachable cycles and the objects they retain. This bounds that source of
retention; it does not identify the cycle's creator or account for all process
memory. The returned count is unreachable objects, not cycles or freed bytes.

The sweep takes no heap census and never enables automatic GC. An existing
DEBUG_SAVEALL session owns its retained garbage, so the sweep leaves it alone.
Native traversal safety and pause time still require exact Windows evidence.
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
        _write('%s unreachable=%d elapsed_ms=%d gc_enabled=%d' % (
            prefix, unreachable, elapsed_ms, 1 if gc.isenabled() else 0))
        return unreachable
    except Exception:
        _write(prefix + ' error=collection_failed')
        return -1
