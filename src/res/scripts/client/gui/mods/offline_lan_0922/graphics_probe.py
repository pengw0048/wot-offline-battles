"""What quality preset a #1513 process is actually running.

Both hidden workers in the 2026-09-09/10 reports logged
``MemoryCriticalController`` force-lowering quality minutes before they died,
and neither log said what the preset had been before or after.  That gap
matters twice over:

* it is the first thing to know before deciding whether disabling a feature
  would buy the worker any address space at all, and
* ``MemoryCriticalController`` lowers ``TERRAIN_QUALITY`` among others, which
  this port's simulation reads through ground probes and BSP collision.  A
  quality change *during* a round is therefore a correctness question, not
  only a memory one, and right now nothing records that it happened.

The contract is read from the stock client, not guessed.  #1513's own
``MemoryCriticalController.__call__`` and ``GraphicsPresets.setSelectedOption``
both index ``BigWorld.graphicsSettings()`` entries as ``t[0]`` = setting name,
``t[1]`` = selected option index, ``t[2]`` = the option list.  The same code
computes ``len(t[2]) - 1`` and calls it the *minimum* quality, so a **higher
index means lower quality** - which is why each value is reported as
``index/last`` rather than on its own.

This only reports what the process already decided.  It never sets anything.
It is diagnostics: every failure is swallowed and the caller continues.
"""

import sys

# Reported in this order.  TEXTURE/FLORA/TERRAIN are the three
# MemoryCriticalController actually lowers; the rest bound what a
# "disable more features" decision could be worth.
INTERESTING_SETTINGS = (
    'TEXTURE_QUALITY',
    'TERRAIN_QUALITY',
    'FLORA_QUALITY',
    'SHADOWS_QUALITY',
    'LIGHTING_QUALITY',
    'EFFECTS_QUALITY',
    'POST_PROCESSING_QUALITY',
    'WATER_QUALITY',
    'DECOR_QUALITY',
    'OBJECT_LOD',
    'FAR_PLANE',
)
MAX_REPORTED = 24


def _bigworld():
    try:
        import BigWorld
        return BigWorld
    except ImportError:
        return None


def snapshot():
    """Return {name: option index}, or None when the API is unavailable."""
    bigworld = _bigworld()
    if bigworld is None:
        return None
    reader = getattr(bigworld, 'graphicsSettings', None)
    if reader is None:
        return None
    try:
        entries = reader()
    except Exception:
        return None
    return _reduce(entries)


def _reduce(entries):
    """Keep each setting's selected index and its lowest available index."""
    if not entries:
        return None
    selected = {}
    try:
        for entry in entries:
            if not entry:
                continue
            name = str(entry[0])
            try:
                index = int(entry[1])
            except (TypeError, ValueError, IndexError):
                index = -1
            last = -1
            try:
                options = entry[2]
                if options is not None:
                    last = len(options) - 1
            except (TypeError, ValueError, IndexError):
                last = -1
            selected[name] = (index, last)
    except (TypeError, IndexError):
        return None
    return selected or None


def format_line(phase, round_id, state=None):
    """Return the one GRAPHICS line for this boundary, or None."""
    state = snapshot() if state is None else state
    if not state:
        return None
    parts = []
    for name in INTERESTING_SETTINGS:
        if name in state:
            parts.append(_render(name, state[name]))
    remaining = sorted(
        name for name in state if name not in INTERESTING_SETTINGS)
    for name in remaining[:max(0, MAX_REPORTED - len(parts))]:
        parts.append(_render(name, state[name]))
    return ('[Offline LAN 0.9.22] GRAPHICS phase=%s round=%s settings=%d %s'
            % (phase, round_id, len(state), ' '.join(parts)))


def _render(name, value):
    """Render one setting as selected/lowest, higher index being lower."""
    index, last = value
    if last < 0:
        return '%s=%d' % (name, index)
    return '%s=%d/%d' % (name, index, last)


def log(phase, round_id):
    """Write one GRAPHICS line. Never raises into a caller."""
    try:
        line = format_line(phase, round_id)
        if line is not None:
            sys.stdout.write(line + '\n')
    except Exception:
        pass
