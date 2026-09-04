from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = (
    ROOT / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import native_mapping_mask


class _Bridge(object):
    def __init__(self, apply_status=0, restore_status=0,
                 restore_statuses=None):
        self.apply_status = apply_status
        self.restore_status = restore_status
        self.restore_statuses = list(restore_statuses or ())
        self.events = []
        self.active = False

    def apply_standard_gameplay_mask(self):
        self.events.append('apply')
        if self.apply_status == 0:
            self.active = True
        return self.apply_status

    def restore_standard_gameplay_mask(self):
        self.events.append('restore')
        status = (self.restore_statuses.pop(0)
                  if self.restore_statuses else self.restore_status)
        if not self.active and status == 0:
            return 102
        if status == 0:
            self.active = False
        return status


class NativeMappingMaskTests(unittest.TestCase):
    def test_native_bridge_wraps_one_mapping_and_restores(self):
        bridge = _Bridge()
        observed = []

        def mapping(space_id, path=None):
            observed.append((space_id, path, bridge.active))
            return 37

        result = native_mapping_mask.call_with_standard_gameplay_mask(
            mapping, (1073741825,), {'path': 'spaces/02_malinovka'},
            bridge)

        self.assertEqual(37, result)
        self.assertEqual(
            [(1073741825, 'spaces/02_malinovka', True)], observed)
        self.assertEqual(['apply', 'restore'], bridge.events)
        self.assertFalse(bridge.active)

    def test_mapping_exception_still_restores_original_mask(self):
        bridge = _Bridge()

        def mapping():
            self.assertTrue(bridge.active)
            raise LookupError('mapping failed')

        with self.assertRaisesRegex(LookupError, 'mapping failed'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                mapping, native_bridge=bridge)

        self.assertEqual(['apply', 'restore'], bridge.events)
        self.assertFalse(bridge.active)

    def test_apply_failure_fails_before_callback(self):
        bridge = _Bridge(apply_status=103)
        called = []

        with self.assertRaisesRegex(
                RuntimeError, 'signature changed.*status 103'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: called.append(True), native_bridge=bridge)

        self.assertEqual([], called)
        self.assertEqual(['apply'], bridge.events)

    def test_failed_native_rollback_is_reported(self):
        bridge = _Bridge(apply_status=108, restore_status=106)

        with self.assertRaisesRegex(RuntimeError, 'rollback.*status 108'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, native_bridge=bridge)

        self.assertEqual(['apply', 'restore'], bridge.events)

    def test_restore_failure_replaces_mapping_result_with_failure(self):
        bridge = _Bridge(restore_status=106)

        with self.assertRaisesRegex(
                RuntimeError, 'protection restore.*status 106'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: 37, native_bridge=bridge)

        self.assertEqual(['apply', 'restore', 'restore'], bridge.events)
        self.assertTrue(bridge.active)

    def test_restore_retries_before_committing_the_python_lease(self):
        bridge = _Bridge(restore_statuses=(106, 0))
        patch = native_mapping_mask._StandardGameplayMaskPatch(bridge)
        patch.apply()

        self.assertTrue(patch.restore())

        self.assertEqual(['apply', 'restore', 'restore'], bridge.events)
        self.assertFalse(bridge.active)
        self.assertFalse(patch._applied)

    def test_unresolved_restore_failure_keeps_the_lease_retryable(self):
        bridge = _Bridge(restore_status=106)
        patch = native_mapping_mask._StandardGameplayMaskPatch(bridge)
        patch.apply()

        with self.assertRaisesRegex(
                RuntimeError, 'protection restore.*status 106'):
            patch.restore()

        self.assertTrue(bridge.active)
        self.assertTrue(patch._applied)
        bridge.restore_status = 0
        self.assertTrue(patch.restore())
        self.assertFalse(bridge.active)
        self.assertFalse(patch._applied)

    def test_missing_native_method_fails_closed(self):
        bridge = object()

        with self.assertRaisesRegex(RuntimeError, 'bridge is incomplete'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, native_bridge=bridge)

    def test_default_path_loads_the_exact_sidecar_bridge(self):
        bridge = _Bridge()
        with mock.patch.object(
                native_mapping_mask, '_load_native_bridge',
                return_value=bridge) as loader:
            self.assertEqual(
                9,
                native_mapping_mask.call_with_standard_gameplay_mask(
                    lambda: 9))

        loader.assert_called_once_with()
        self.assertEqual(['apply', 'restore'], bridge.events)


if __name__ == '__main__':
    unittest.main()
