"""Load native geometry and the background navigation/spotting workers."""
from __future__ import print_function

import os
import sys


MODULE_NAME = 'offline_math_batch_native'
_backend = None
_attempted = sys.platform != 'win32'
_calls = {'translation_fraction': 0, 'slide_translation': 0,
          'rotation_fraction': 0, 'contact_roster': 0, 'world_run': 0}
_fallbacks = 0
_reported_failure = False
_world_failures = 0
_reported_world_failure = False


def _load():
    global _backend, _attempted
    if _backend is not None or _attempted:
        return _backend
    _attempted = True
    path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                        'mods', '0.9.22.0.1', MODULE_NAME + '.pyd')
    try:
        import imp
        _backend = imp.load_dynamic(MODULE_NAME, path)
        sys.modules[MODULE_NAME] = _backend
    except Exception as error:
        _report_failure(error)
    return _backend


def _report_failure(error):
    global _reported_failure
    if not _reported_failure:
        _reported_failure = True
        sys.stdout.write('[Offline LAN 0.9.22] native computation unavailable '
                         'for an operation; using Python: %s\n' % error)


def call(method, *args):
    """Read borrowed inputs under the GIL, with no serialization or queues."""
    global _fallbacks
    backend = _load()
    if backend is None:
        if method == 'contact_roster':
            _fallbacks += 1
        return None
    try:
        result = getattr(backend, method)(*args)
        if result is not None:
            if not _calls[method]:
                sys.stdout.write('[Offline LAN 0.9.22] native geometry '
                                 'active: %s\n' % method)
            _calls[method] += 1
            return result
        _report_failure('unsupported geometry input')
    except Exception as error:
        _report_failure(error)
    _fallbacks += 1
    return None


def snapshot():
    """Return cumulative counters for diagnostics and caller benchmarks."""
    result = dict(_calls)
    result.update(loaded=_backend is not None, fallbacks=_fallbacks,
                  world_failures=_world_failures)
    return result


def report_world_failure(error):
    """Count rejected motion operations without disabling or replaying them."""
    global _world_failures, _reported_world_failure
    _world_failures += 1
    if not _reported_world_failure:
        _reported_world_failure = True
        try:
            sys.stdout.write('[Offline LAN 0.9.22] native world operation '
                             'failed; blocking this motion without replay: '
                             '%s\n' % error)
        except Exception:
            pass


def contact_roster(tanks, owner_ids, dt, previous_ram_contacts):
    """Synchronously compute frozen roster geometry; never call the engine."""
    return call('contact_roster', tanks, owner_ids, dt,
                list(previous_ram_contacts or ()))


def world_available():
    backend = _load()
    return backend is not None and hasattr(backend, 'world_run')


def world_run(snapshot, dispatcher):
    """Never replay a world operation after an engine callback has started."""
    global _fallbacks
    backend = _load()
    if backend is None or not hasattr(backend, 'world_run'):
        _fallbacks += 1
        return None
    # Callback exceptions propagate unchanged. The extension returns None only
    # for an unsupported snapshot before invoking the dispatcher at all.
    result = backend.world_run(snapshot, dispatcher)
    if result is None:
        _fallbacks += 1
        _report_failure('unsupported world input before engine dispatch')
        return None
    if result not in (0, 1, 2):
        raise RuntimeError('Native world stage failed after engine dispatch')
    if not _calls['world_run']:
        sys.stdout.write('[Offline LAN 0.9.22] native computation '
                         'active: world_run\n')
    _calls['world_run'] += 1
    return result
