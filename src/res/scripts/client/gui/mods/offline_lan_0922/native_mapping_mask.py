from __future__ import print_function


_STATUS_OPERATIONS = {
    101: 'mapping mask was already active',
    102: 'mapping mask was not active',
    103: '#1513 mapping signature changed',
    104: 'mapping code protection enable',
    105: 'mapping instruction-cache flush',
    106: 'mapping code protection restore',
    107: 'mapping mask verification',
    108: 'mapping mask rollback',
}

_RESTORE_ATTEMPTS = 2


class NativeMappingMaskError(RuntimeError):
    pass


def _load_native_bridge():
    # The exact client statically embeds Python and omits _ctypes. Reuse the
    # sidecar loader that already validates and loads our #1513-only x86
    # bridge from beside the wotmod.
    from gui.mods.offline_lan_0922 import instance_guard
    return instance_guard._load_native_bridge()


def _native_status(bridge, method_name):
    method = getattr(bridge, method_name, None)
    if not callable(method):
        raise NativeMappingMaskError(
            '#1513 native mapping-mask bridge is incomplete: %s' %
            method_name)
    try:
        return int(method())
    except (TypeError, ValueError) as error:
        raise NativeMappingMaskError(
            '#1513 native mapping-mask bridge returned an invalid status: '
            '%s' % error)


def _raise_status(status):
    status = int(status)
    operation = _STATUS_OPERATIONS.get(
        status, '#1513 native mapping mask')
    raise NativeMappingMaskError(
        '%s failed with native status %d' % (operation, status))


class _StandardGameplayMaskPatch(object):
    """Own one reversible CTF-mask patch in the exact native wrapper."""

    def __init__(self, bridge):
        self._bridge = bridge
        self._applied = False

    def apply(self):
        status = _native_status(
            self._bridge, 'apply_standard_gameplay_mask')
        if status != 0:
            # The bridge rolls back every ordinary post-write failure itself.
            # Only its explicit rollback-failed status permits this caller to
            # attempt another restore; an already-active status may belong to
            # an outer/re-entrant mapping and must never be undone here.
            if status == 108:
                try:
                    restore_status = _native_status(
                        self._bridge, 'restore_standard_gameplay_mask')
                except Exception:
                    restore_status = None
                if restore_status not in (0, 102):
                    _raise_status(108)
            _raise_status(status)
        self._applied = True

    def restore(self):
        if not self._applied:
            return False
        last_error = None
        for unused_attempt in range(_RESTORE_ATTEMPTS):
            try:
                status = _native_status(
                    self._bridge, 'restore_standard_gameplay_mask')
            except Exception as error:
                last_error = error
                continue
            # A previous native attempt may have completed the restoration but
            # lost its return value.  In that case NOT_ACTIVE is the same
            # confirmed terminal state as an ordinary successful restore.
            if status in (0, 102):
                self._applied = False
                return True
            try:
                _raise_status(status)
            except NativeMappingMaskError as error:
                last_error = error
        # Keep ownership on every unresolved failure.  Clearing this flag while
        # the native bridge still reports its patch active makes all later
        # apply calls fail with ALREADY_ACTIVE and poisons the process.
        raise last_error


def call_with_standard_gameplay_mask(callback, args=(), kwargs=None,
                                     native_bridge=None):
    """Call one #1513 geometry mapping with only standard CTF items."""
    if not callable(callback):
        raise TypeError('native mapping callback must be callable')
    if kwargs is None:
        kwargs = {}
    if native_bridge is None:
        native_bridge = _load_native_bridge()
    patch = _StandardGameplayMaskPatch(native_bridge)
    patch.apply()
    try:
        return callback(*args, **kwargs)
    finally:
        patch.restore()
