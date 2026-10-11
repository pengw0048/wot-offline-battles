"""Complete navigation-oracle ownership and exception boundary contracts."""
import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import native_math, native_navigation_query as query


class Backend:
    def __init__(self, result=(1, 9, 3)):
        self.result = result
        self.calls = []

    def nav_query_filter(self, *surface):
        return True

    def nav_query_run(self, snapshot, capabilities, hooks):
        self.calls.append((snapshot, capabilities, hooks))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def owner(vector=None):
    return types.SimpleNamespace(
        _avatar=types.SimpleNamespace(spaceID=7), _bots=object(),
        _runtime=types.SimpleNamespace(
            bigworld=types.SimpleNamespace(wg_collideSegment=mock.Mock(),
                                           wg_collideWater=mock.Mock(return_value=None)),
            math=types.SimpleNamespace(Vector3=vector or mock.Mock())))


ARGS = ((0., 0., 0.), (0., 0., 12.), 18., .48, .38)


class NativeNavigationQueryTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        for field in ('_reported_active', '_reported_failure'):
            patch = mock.patch.object(query, field, False)
            patch.start()
            self.addCleanup(patch.stop)

    def test_complete_oracle_binds_raw_capabilities_once_and_logs_activation(self):
        target, backend = owner(), Backend()
        oracle = query.Oracle(target, backend)
        self.assertEqual((1, 9, 3), oracle.run(*ARGS))
        self.assertEqual((1, 9, 3), oracle.run(*ARGS))
        self.assertIs(backend.calls[0][1][0], target._runtime.bigworld.wg_collideSegment)
        self.assertIs(backend.calls[0][1], backend.calls[1][1])
        self.assertEqual(1, self.output.getvalue().count('active: nav_query_run'))
        self.assertEqual(dict(calls=2, failures=0, rays=18, waters=6, available=True), oracle.snapshot())

    def test_missing_backend_is_visible_and_create_does_not_break_worker_start(self):
        with mock.patch.object(native_math, '_load', return_value=None):
            self.assertIsNone(query.Oracle.create(owner()))
            self.assertIsNone(query.Oracle.create(owner()))
        self.assertEqual(1, self.output.getvalue().count('unavailable'))

    def test_unsupported_before_dispatch_is_the_only_replayable_none(self):
        target, backend = owner(), Backend(None)
        self.assertIsNone(query.Oracle(target, backend).run(*ARGS))
        target._runtime.bigworld.wg_collideSegment.assert_not_called()
        self.assertIn('before engine dispatch', self.output.getvalue())

    def test_after_dispatch_exception_is_unknown_and_not_replayable(self):
        oracle = query.Oracle(owner(), Backend(RuntimeError('failed after ray')))
        self.assertEqual((-1, -1, -1), oracle.run(*ARGS))
        self.assertEqual((-1, -1, -1), oracle.run(*ARGS))
        self.assertEqual(2, oracle.failures)
        self.assertEqual(1, self.output.getvalue().count('failed after ray'))

    def test_stale_avatar_space_or_bot_owner_cannot_query(self):
        for change in ('avatar', 'space', 'bots'):
            target, backend = owner(), Backend()
            oracle = query.Oracle(target, backend)
            if change == 'avatar':
                target._avatar = types.SimpleNamespace(spaceID=7)
            elif change == 'space':
                target._avatar.spaceID = 8
            else:
                target._bots = object()
            self.assertEqual((-1, 0, 0), oracle.run(*ARGS))
            self.assertEqual([], backend.calls)

    def test_water_capability_contains_constructor_and_engine_failure(self):
        vector = mock.Mock(side_effect=RuntimeError('Vector3 failed'))
        target = owner(vector)
        oracle = query.Oracle(target, Backend())
        self.assertIsNone(oracle._capabilities[1]((0., 20., 0.), (0., -5., 0.), False))
        target._runtime.bigworld.wg_collideWater.assert_not_called()
        vector.side_effect = None
        target._runtime.bigworld.wg_collideWater.side_effect = RuntimeError('water failed')
        self.assertIsNone(oracle._capabilities[1]((0., 20., 0.), (0., -5., 0.), False))


if __name__ == '__main__':
    unittest.main()
