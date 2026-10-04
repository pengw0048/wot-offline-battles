"""Bounded passive tracing for unlocalized replay/exit stalls.

No waits, background engine calls, process termination, queue changes, or new
playback clock. Successful game.fini also notifies the native process owner.
Every wrapped method receives exactly its original arguments.
Trace references deliberately last through game.fini, after mod cleanup.
"""
from __future__ import print_function
import sys
import time

_EXIT_HOOKS = []
_TRACE_ID = 0
_WALL = time.time


def _log(kind, value):
    try:
        sys.stdout.write('[Offline LAN 0.9.22] %s %s\n' % (kind, value))
    except (IOError, ValueError, AttributeError):
        return


def _wrap(owner, attribute, label, on_return=None):
    original = getattr(owner, attribute, None)
    if not callable(original):
        return False
    for item in _EXIT_HOOKS:
        if item[0] is owner and item[1] == attribute and original is item[3]:
            return False

    def traced(*args, **kwargs):
        global _TRACE_ID
        _TRACE_ID += 1
        call_id = _TRACE_ID
        started = _WALL()
        _log('EXIT_TRACE', 'begin id=%s stage=%s wall=%.6f' %
             (call_id, label, started))
        outcome = 'raised'
        try:
            result = original(*args, **kwargs)
            outcome = 'returned'
            return result
        finally:
            _log('EXIT_TRACE', 'end id=%s stage=%s result=%s duration_ms=%.3f' %
                 (call_id, label, outcome, max(0.0, _WALL()-started)*1000.0))
            if outcome == 'returned' and on_return is not None:
                try:
                    _log('EXIT_TRACE', 'player_fini_notified=%s' % on_return())
                except Exception as error:
                    _log('EXIT_TRACE', 'finish_signal_failed=%s' % error)

    try:
        setattr(owner, attribute, traced)
    except (AttributeError, TypeError):
        _log('EXIT_TRACE', 'unavailable stage=%s' % label)
        return False
    _EXIT_HOOKS.append((owner, attribute, original, traced))
    return True


def install_exit_trace():
    """Instrument existing shutdown phases, without reordering native cleanup."""
    from .worker_presentation import signal_player_finished
    module_targets = (
        ('game', ('fini',)),
        ('BigWorld', ('quit', 'resetEntityManager', 'clearAllSpaces')),
        ('gui_personality', ('fini',)),
        ('PostProcessing.Phases', ('fini',)),
    )
    count = 0
    for module_name, attributes in module_targets:
        module = sys.modules.get(module_name)
        if module is not None:
            for attribute in attributes:
                label = module_name + '.' + attribute
                count += int(_wrap(module, attribute, label,
                                   signal_player_finished if label == 'game.fini' else None))
    game = sys.modules.get('game')
    for attribute in ('g_postProcessing',):
        owner = getattr(game, attribute, None)
        if owner is not None:
            count += int(_wrap(owner, 'fini', 'game.'+attribute+'.fini'))
    for module_name, attributes in (
            ('SoundGroups', ('destroy',)), ('Settings', ('save',))):
        owner = getattr(sys.modules.get(module_name), 'g_instance', None)
        if owner is not None:
            for attribute in attributes:
                count += int(_wrap(owner, attribute, module_name+'.g_instance.'+attribute))
    if count:
        _log('EXIT_TRACE', 'installed=%s player_fini_notification=True' % count)
    return count


def remove_exit_trace():
    """Test/explicit teardown only; do not remove halfway through native fini."""
    while _EXIT_HOOKS:
        owner, name, original, traced = _EXIT_HOOKS.pop()
        if getattr(owner, name, None) is traced:
            setattr(owner, name, original)


def observe_replay_pump(client, operation):
    started = _WALL()
    prior = getattr(client, '_trace_pump_wall', None)
    old_rows = getattr(client, '_replay_rows', 0)
    try:
        return operation()
    finally:
        # A diagnostic failure must not turn a completed pump into a replay
        # error, nor shadow the operation's original exception/return value.
        try:
            _replay_trace(client, started, prior, old_rows)
        except (AttributeError, TypeError, ValueError, ReferenceError):
            pass


def _replay_trace(client, started, prior, old_rows):
    finished = _WALL()
    client._trace_pump_wall = started
    if not getattr(client, '_replay_ready', False):
        return
    duration = max(0.0, finished-started)
    gap = max(0.0, started-prior) if prior is not None else 0.0
    elapsed = max(0.0, client._clock()-client._replay_started)
    queue = getattr(client, '_replay_queue', ())
    presented = getattr(client, '_replay_present_limit', 0.0)
    reader = getattr(client, 'reader', None)
    reader_queue = getattr(reader, '_queue', ())
    reader_age = max(0.0, finished-getattr(reader, '_activity', finished))
    next_at = queue[0]['t'] if queue else None
    # This measures playback delivery lag, not recorded vehicle speed.
    lag = max(0.0, elapsed-presented)
    stalled = duration >= 0.25 or gap >= 0.25 or lag >= 0.25
    if stalled:
        if finished < getattr(client, '_trace_spike_at', 0.0):
            return
        client._trace_spike_at = finished+1.0
    elif finished < getattr(client, '_trace_report_at', 0.0):
        return
    client._trace_report_at = finished+10.0
    _log('REPLAY_PACING',
         'wall=%.6f time=%.3f rows=%s applied=%s pump_ms=%.3f gap_ms=%.3f lag_ms=%.3f pending=%s next_t=%r decoder_queue=%s decoder_bytes=%s decoder_age_ms=%.3f decoder_done=%s error=%r' %
         (finished, elapsed, getattr(client, '_replay_rows', 0),
          getattr(client, '_replay_rows', 0)-old_rows, duration*1000.0,
          gap*1000.0, lag*1000.0, len(queue), next_at, len(reader_queue),
          getattr(reader, '_written_bytes', 0)-getattr(reader, '_read_bytes', 0),
          reader_age*1000.0, getattr(reader, '_done', None), getattr(reader, 'error', None)))
