# -*- coding: utf-8 -*-
"""Select and report the world-collision preparation backend.

The hidden worker owns one process-wide choice, made before a battle starts
and logged once, so a capture can never be attributed to an implementation
that did not run:

``python``
    The unchanged inline sweep preparation.
``batch``
    The same arithmetic, in the same order, evaluated once per sweep by
    :mod:`world_collision_prep`.
``native``
    The exact-build extension performing the ``batch`` arithmetic, with the
    Python module retained for every value the extension does not own.
``native-shadow``
    Both of the above, compared value by value.  This is a correctness run on
    the exact client; it doubles the computation and never measures anything.

A native load or self-test failure selects ``python`` and records the reason.
The effective name, not the requested name, labels the run.
"""

from __future__ import print_function

import os


BACKENDS = ('python', 'batch', 'native', 'native-shadow')
DEFAULT_BACKEND = 'python'
BACKEND_ENVIRONMENT = 'OFFLINE_LAN_0922_COMPUTE_BACKEND'
BACKEND_FILENAME = 'compute_backend.txt'

_requested = None
_effective = None
_error = None
_module = None
_preparation = None
_counts = {}


def _configured_backend(environ):
    value = environ.get(BACKEND_ENVIRONMENT)
    if value:
        return value.strip().lower(), 'environment'
    try:
        from gui.mods.offline_lan_0922 import user_config
        path = user_config.user_data_path(BACKEND_FILENAME)
    except (ImportError, OSError, IOError):
        return DEFAULT_BACKEND, 'default'
    try:
        stream = open(path, 'rb')
    except (IOError, OSError):
        return DEFAULT_BACKEND, 'default'
    try:
        value = stream.read(64).decode('ascii', 'ignore')
    finally:
        stream.close()
    value = value.strip().lower()
    return (value, 'file') if value else (DEFAULT_BACKEND, 'default')


def select_backend(backend=None, environ=None, loader=None):
    """Choose the backend for this process and return its effective name."""
    global _requested, _effective, _error, _module, _preparation, _counts
    environ = os.environ if environ is None else environ
    source = 'call'
    if backend is None:
        backend, source = _configured_backend(environ)
    backend = str(backend).strip().lower()
    _counts = {}
    _module = None
    _preparation = None
    _error = None
    if backend not in BACKENDS:
        _error = 'unknown compute backend %r from %s' % (backend, source)
        _requested = backend
        _effective = DEFAULT_BACKEND
        return _effective
    _requested = backend
    if backend in ('native', 'native-shadow'):
        try:
            _module = (native_module() if loader is None else loader())
        except Exception as error:
            _error = '%s: %s' % (type(error).__name__, error)
            _module = None
        if _module is None:
            _effective = DEFAULT_BACKEND
            return _effective
    _effective = backend
    return _effective


def native_module():
    """Load and validate the exact-build compute extension."""
    from gui.mods.offline_lan_0922 import native_compute_bridge
    return native_compute_bridge.load()


def selected_backend():
    """Return the backend that is actually running, selecting one if needed."""
    if _effective is None:
        select_backend()
    return _effective


def requested_backend():
    if _effective is None:
        select_backend()
    return _requested


def preparation():
    """Return the sweep preparation, or ``None`` for the unchanged path.

    One sweep resolves this per call, so it stays a cached attribute lookup
    rather than repeated configuration work.
    """
    global _preparation
    if _preparation is not None:
        return _preparation
    backend = selected_backend()
    if backend == 'python':
        return None
    from gui.mods.offline_lan_0922 import world_collision_prep
    if backend == 'native':
        _preparation = world_collision_prep.native_preparation(_module)
    elif backend == 'native-shadow':
        _preparation = world_collision_prep.shadow_preparation(_module)
    else:
        _preparation = world_collision_prep.python_preparation()
    return _preparation


def note(name, value=1):
    """Record a bounded backend event for the capture report."""
    _counts[name] = _counts.get(name, 0) + int(value)


def demote(reason):
    """Relabel this process after a native failure, keeping the run honest.

    The caller has already recovered through the unchanged Python path. The
    backend name must stop claiming native so no later capture is reported as
    native evidence.
    """
    global _effective, _error, _preparation
    note('native_failures')
    if _effective in ('native', 'native-shadow'):
        _effective = 'batch'
        _error = 'demoted after %s' % (reason,)
        from gui.mods.offline_lan_0922 import world_collision_prep
        _preparation = world_collision_prep.python_preparation()


def status():
    """Return the one-line-per-battle record of what actually ran."""
    return {
        'requested': requested_backend(),
        'backend': selected_backend(),
        'error': _error,
        'module': getattr(_module, '__file__', None),
        'counts': dict(_counts),
    }


def describe():
    record = status()
    return ('compute backend requested=%s effective=%s module=%s error=%s' % (
        record['requested'], record['backend'], record['module'],
        record['error']))
