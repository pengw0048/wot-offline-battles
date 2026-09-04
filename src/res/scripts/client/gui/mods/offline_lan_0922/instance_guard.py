from __future__ import print_function

import os
import sys


ALLOW_MULTIPLE_CLIENTS_ENV = 'OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS'
NATIVE_MODULE_NAME = 'offline_instance_guard_native'
NATIVE_FILENAME = NATIVE_MODULE_NAME + '.pyd'
GAME_VERSION_DIR = '0.9.22.0.1'

_GUARD_STATUS_OPERATIONS = {
    1: 'WGC holder validation',
    2: 'WGC wrapper lookup',
    3: 'WGC wrapper validation',
    4: 'WGC processing-state validation',
    5: 'WGC API lookup',
    6: 'wgc_api.dll lookup',
    7: 'WGC API validation',
    8: 'WGC child validation',
    9: 'WGC holder postcondition',
    10: 'WGC API teardown postcondition',
    11: 'WGC child teardown postcondition',
    12: 'WGC state teardown postcondition',
    13: 'WGC named-mutex teardown postcondition',
    14: 'WGC named-mutex result probe',
}

_attempted = False
_release_succeeded = False
_release_error = None
_native_bridge = None


class ClientInstanceGuardError(RuntimeError):
    """The #1513 WGC client guard could not be torn down safely."""

    def __init__(self, operation, error_code):
        self.operation = operation
        self.error_code = int(error_code)
        RuntimeError.__init__(
            self, '%s failed with native status %d' % (
                self.operation, self.error_code))


def _multiple_clients_requested(environ):
    value = environ.get(ALLOW_MULTIPLE_CLIENTS_ENV)
    try:
        value = value.strip()
    except AttributeError:
        return False
    return value == '1'


def _native_bridge_path(executable=None):
    executable = sys.executable if executable is None else executable
    game_root = os.path.dirname(os.path.abspath(executable))
    return os.path.join(
        game_root, 'mods', GAME_VERSION_DIR, NATIVE_FILENAME)


def _load_native_bridge(path=None, imp_module=None):
    global _native_bridge
    if _native_bridge is not None:
        return _native_bridge
    path = _native_bridge_path() if path is None else path
    if not os.path.isfile(path):
        raise ImportError('native instance guard bridge is missing: %s' % path)
    if imp_module is None:
        import imp as imp_module
    bridge = imp_module.load_dynamic(NATIVE_MODULE_NAME, path)
    for method_name in (
            'release_client_guard',
            'hide_process_windows',
            'show_process_windows'):
        if not callable(getattr(bridge, method_name, None)):
            raise ImportError(
                'native instance guard bridge is missing %s' % method_name)
    # Keep the sidecar importable by worker_presentation after this explicit
    # path-based load; native extensions cannot be imported out of a wotmod.
    sys.modules[NATIVE_MODULE_NAME] = bridge
    _native_bridge = bridge
    return bridge


def _raise_release_failure(status):
    status = int(status)
    operation = _GUARD_STATUS_OPERATIONS.get(
        status, 'offline_instance_guard_native')
    raise ClientInstanceGuardError(operation, status)


def _release_native(native_bridge=None):
    # Load and exact-build-validate the bridge before touching WGC. A missing
    # or incompatible sidecar therefore leaves the normal client untouched.
    if native_bridge is None:
        native_bridge = _load_native_bridge()
    # guiModsInit is not #1513's mutex-owner thread. The bridge therefore uses
    # the engine's own complete WGC cleanup thunk, which destroys the API and
    # releases its AppMutex while retaining the wrapper for normal shutdown.
    status = int(native_bridge.release_client_guard())
    if status != 0:
        _raise_release_failure(status)
    return True


def _window_operation(method_name, native_bridge=None):
    if native_bridge is None:
        native_bridge = _load_native_bridge()
    result = int(getattr(native_bridge, method_name)())
    if result < 0:
        raise ClientInstanceGuardError(method_name, -result)
    return result


def hide_process_windows(native_bridge=None):
    """Hide and remember this process's visible top-level windows."""
    return _window_operation('hide_process_windows', native_bridge)


def show_process_windows(native_bridge=None):
    """Restore top-level windows hidden through this bridge."""
    return _window_operation('show_process_windows', native_bridge)


def release_if_requested(environ=None, releaser=None):
    """Tear down #1513's WGC client guard once for an opted-in process."""
    global _attempted, _release_error, _release_succeeded

    environ = os.environ if environ is None else environ
    if not _multiple_clients_requested(environ):
        return False
    if _attempted:
        if _release_error is not None:
            raise _release_error
        return _release_succeeded

    _attempted = True
    try:
        if releaser is None:
            releaser = _release_native
        _release_succeeded = bool(releaser())
        if not _release_succeeded:
            raise ClientInstanceGuardError('WGC client guard teardown', 0)
        return True
    except Exception as error:
        _release_error = error
        raise
