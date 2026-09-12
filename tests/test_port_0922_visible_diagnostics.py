import contextlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))

from gui.mods.offline_lan_0922 import visible_diagnostics as diagnostics
from gui.mods.offline_lan_0922.battle_runtime import _FrameDiagnostics
import test_port_0922_battle_runtime as fixtures


class VisibleTimingTests(unittest.TestCase):

    def test_nested_calls_remove_children_and_accumulate_repeated_work(self):
        clock = mock.Mock(side_effect=[0, 1, 3, 4, 7, 10])
        costs = diagnostics.FrameCosts(clock)
        parent = costs.start('local')
        first = costs.start('local.ground')
        costs.stop(first)
        second = costs.start('local.ground')
        costs.stop(second)
        costs.stop(parent)

        snapshot = costs.snapshot()
        self.assertFalse(snapshot['failed'])
        self.assertEqual((1, 10, 5, 10), snapshot['stages']['local'])
        self.assertEqual((2, 5, 5, 3), snapshot['stages']['local.ground'])
        costs.rows['local'][0] += 1
        self.assertEqual(1, snapshot['stages']['local'][0])
        json.dumps(snapshot)

    def test_sampler_rotates_through_periodic_feed_phases(self):
        clock = mock.Mock()
        selected = [frame for frame in range(1, 65)
                    if diagnostics.new_frame(frame, clock) is not None]
        self.assertEqual(8, len(selected))
        self.assertEqual(list(range(8)), [(frame - 1) // 8 for frame in selected])
        self.assertEqual(list(range(8)), [(frame - 1) % 8 for frame in selected])
        for invalid in (0, -1, None, 'invalid', float('inf')):
            self.assertIsNone(diagnostics.new_frame(invalid, clock))
        clock.assert_not_called()

    def test_observation_never_changes_arguments_return_or_exception(self):
        value = object()
        error = RuntimeError('operation failed')
        for clock_values in ([0, 1], [RuntimeError('clock failed')],
                             [1, 0], [0, float('nan')], [float('inf')]):
            for outcome in (value, error):
                with self.subTest(clock=clock_values, outcome=outcome):
                    costs = diagnostics.FrameCosts(mock.Mock(side_effect=clock_values))
                    owner = types.SimpleNamespace(_visible_frame_costs=costs)
                    operation = mock.Mock(return_value=outcome)
                    if outcome is error:
                        operation.side_effect = error
                        with self.assertRaises(RuntimeError) as caught:
                            diagnostics.call(owner, 'sync.pose', operation,
                                             value, now=1.25)
                        self.assertIs(error, caught.exception)
                    else:
                        self.assertIs(value, diagnostics.call(
                            owner, 'sync.pose', operation, value, now=1.25))
                    operation.assert_called_once_with(value, now=1.25)
                    self.assertEqual([], costs.stack)
                    self.assertEqual(clock_values != [0, 1], costs.snapshot()['failed'])

    def test_decorator_is_inert_without_a_sample_and_unwinds_on_failure(self):
        class Owner:
            _visible_frame_costs = None

            @diagnostics.measured('local.motion')
            def operation(self, callback, **kwargs):
                return callback(**kwargs)

        owner = Owner()
        callback = mock.Mock(return_value=object())
        self.assertIs(callback.return_value, owner.operation(callback, speed=8.0))
        callback.assert_called_once_with(speed=8.0)
        owner._visible_frame_costs = diagnostics.FrameCosts(lambda: 0.0)
        callback.side_effect = ValueError('motion failed')
        with self.assertRaisesRegex(ValueError, 'motion failed'):
            owner.operation(callback, speed=8.0)
        self.assertEqual([], owner._visible_frame_costs.stack)
        self.assertEqual((1, 0, 0, 0),
                         owner._visible_frame_costs.snapshot()['stages']['local.motion'])

    def test_invalid_scope_or_excess_nesting_discards_only_the_sample(self):
        for invalid in ('unknown', 'depth', 'order', 'open'):
            with self.subTest(invalid=invalid):
                costs = diagnostics.FrameCosts(lambda: 0.0)
                first = costs.start('local')
                if invalid == 'unknown':
                    costs.start('unbounded actor identifier')
                elif invalid == 'depth':
                    for unused in range(diagnostics.MAX_DEPTH):
                        costs.start('local.motion')
                elif invalid == 'order':
                    costs.start('local.motion')
                    costs.stop(first)
                self.assertEqual({'failed': True, 'stages': {}}, costs.snapshot())
                costs.stop(first)
                self.assertEqual([], costs.stack)

    def test_many_calls_retain_only_fixed_numeric_rows(self):
        costs = diagnostics.FrameCosts(lambda: 0.0)
        for unused in range(1000):
            for name in diagnostics.STAGES:
                costs.stop(costs.start(name))
        self.assertEqual(len(diagnostics.STAGES), len(costs.rows))
        self.assertTrue(all(row == (1000, 0, 0, 0)
                            for row in costs.snapshot()['stages'].values()))


class VisibleWindowTests(unittest.TestCase):

    def test_sample_denominator_slow_frame_correlation_and_window_reset(self):
        wall = [0.0]
        payloads = []
        window = _FrameDiagnostics(clock=lambda: wall[0], writer=payloads.append,
                                   window_seconds=100.0)
        first = {'failed': False, 'stages': {
            'sync': (1, .008, .004, .008),
            'sync.pose': (2, .004, .004, .003)}}
        second = {'failed': False, 'stages': {'sync': (1, .004, .004, .004)}}
        snapshots = [first, None, {'failed': True, 'stages': {}}, None, second]
        for snapshot in snapshots:
            frame = window.begin(wall[0], .1)
            wall[0] += .01
            window.finish(frame, wall[0] - .01, .1, .1,
                          {'sync': .006, 'local_ground': .001}, {},
                          {'role': 'guest'}, visible=snapshot)
            wall[0] += .09
        window.begin(wall[0], .1)
        window.flush()
        summary = window.snapshot()['visible_costs']
        self.assertEqual((5, 2, 1), (summary['window_frames'],
                                     summary['sampled_frames'], summary['failed_frames']))
        self.assertEqual(6.0, summary['stages']['sync']['total_ms_per_sample'])
        self.assertEqual(4.0, summary['stages']['sync']['self_ms_per_sample'])
        self.assertEqual(1.0, summary['stages']['sync.pose']['calls_per_sample'])
        self.assertEqual(2.0, summary['stages']['sync.pose']['total_ms_per_sample'])
        self.assertEqual(3.0, summary['stages']['sync.pose']['max_call_ms'])
        self.assertEqual(4.0, summary['stages']['sync.pose']['max_frame_ms'])
        self.assertEqual(1.0, window.snapshot()['python_details_ms']['local_ground']['avg_ms'])
        slow = [json.loads(line.split('visible_slow ', 1)[1])
                for line in payloads[0].splitlines() if 'visible_slow ' in line]
        self.assertTrue(any(row['cause'] == 1 and row['costs'] ==
                            json.loads(json.dumps(first)) for row in slow))
        self.assertTrue(all(len(line) < 7168 for line in payloads[0].splitlines()))
        self.assertIsNone(window._visible_summary())


class VisibleRuntimeTests(unittest.TestCase):

    def test_moving_and_aiming_pose_stream_keeps_native_writes_and_motion_state(self):
        def replay(sampled, kind):
            native, unused = fixtures.RemotePresentationWriteTests()._vehicles()
            runtime = fixtures._runtime()
            battle = fixtures.BattleRuntime(runtime)
            calls = []

            def pose(engine_id, position, rotation, **kwargs):
                calls.append(('pose', engine_id, tuple(position), rotation, kwargs['now']))
                return native.set_pose(position, rotation, **kwargs)

            def aim(engine_id, *angles):
                calls.append(('aim', engine_id, angles))
                return native.set_aim(*angles)

            battle._binding = types.SimpleNamespace(set_vehicle_pose=pose,
                                                     update_vehicle_aim=aim)
            battle._update_bot_tracks = mock.Mock(return_value=True)
            record = {'engine_id': 11, 'kind': kind, 'state': {'speed': 8.0}}
            positions = []
            for frame in range(128):
                runtime.bigworld.now = 1.0 + frame / 60.0
                costs = diagnostics.new_frame(frame + 1, lambda: 0.0) if sampled else None
                battle._visible_frame_costs = costs
                # Translation, turning, aim-only movement and exact repeats
                # all retain their original timestamps and cache behavior.
                tick = min(frame, 64)
                battle._apply_record_pose(record, dict(
                    x=0.0, y=0.0, z=tick / 10.0, yaw=tick / 100.0,
                    pitch=0.1, roll=0.2,
                    aim_yaw=min(frame, 96) / 50.0, gun_pitch=0.1))
                positions.append((tuple(native.matrix.translation),
                                  native.matrix.yaw, native.speed,
                                  native._last_pose_time,
                                  native.aim.turretMatrix.yaw))
                if costs is not None:
                    self.assertFalse(costs.snapshot()['failed'])
            return (calls, positions, battle._update_bot_tracks.call_count,
                    native.matrix.rotation_writes, native.matrix.translation_writes)

        for kind in ('bot', 'player'):
            with self.subTest(kind=kind):
                self.assertEqual(replay(False, kind), replay(True, kind))

    def test_frame_observer_is_bound_only_to_selected_visible_phases(self):
        for frame_id, worker, enabled in ((1, False, True), (2, False, True),
                                           (1, True, True), (1, False, False)):
            with self.subTest(frame=frame_id, worker=worker, enabled=enabled):
                runtime = fixtures._runtime()
                runtime.bigworld.now = 1.0
                battle = fixtures.BattleRuntimeContractTests()._live_frame_battle(runtime)
                battle._worker_mode = worker
                window = types.SimpleNamespace(enabled=enabled,
                    begin=mock.Mock(return_value=frame_id), finish=mock.Mock())
                battle._frame_diagnostics = window
                events = []

                def observe(name, argument):
                    events.append((name, argument, battle._visible_frame_costs))

                battle._sync = types.SimpleNamespace(
                    advance=lambda now: observe('sync', now))
                battle._drive_local = lambda dt: observe('local', dt)
                battle._tick_critical_states = lambda dt: observe('critical', dt)
                battle._advance_player_fire_authority = mock.Mock()
                battle._publish_player_environment = mock.Mock()
                with contextlib.redirect_stdout(io.StringIO()):
                    battle._frame()
                battle._fail.assert_not_called()
                self.assertIsNone(battle._visible_frame_costs)
                self.assertEqual(1.0, events[0][1])
                sampled = frame_id == 1 and enabled and not worker
                for name, argument, observer in events:
                    self.assertEqual(sampled and name in ('sync', 'local'),
                                     observer is not None)
                    if name == 'local':
                        self.assertAlmostEqual(.02, argument)
                if enabled:
                    snapshot = window.finish.call_args.kwargs['visible']
                    self.assertEqual(sampled, snapshot is not None)
                    if sampled:
                        self.assertFalse(snapshot['failed'])
                        self.assertEqual({'sync', 'local'}, set(snapshot['stages']))

    def test_real_phase_failures_keep_round_failure_and_clear_observer(self):
        for phase in ('sync', 'local'):
            with self.subTest(phase=phase):
                runtime = fixtures._runtime()
                runtime.bigworld.now = 1.0
                battle = fixtures.BattleRuntimeContractTests()._live_frame_battle(runtime)
                battle._frame_diagnostics = _FrameDiagnostics(
                    clock=lambda: 0.0, writer=lambda payload: None)
                error = RuntimeError('phase failed')
                observers = []

                def fail(unused):
                    observers.append(battle._visible_frame_costs)
                    raise error

                if phase == 'sync':
                    battle._sync = types.SimpleNamespace(advance=fail)
                else:
                    battle._drive_local = fail
                with contextlib.redirect_stdout(io.StringIO()):
                    battle._frame()
                battle._fail.assert_called_once_with(error)
                battle._schedule.assert_not_called()
                self.assertEqual(1, len(observers))
                self.assertIsNotNone(observers[0])
                self.assertEqual([], observers[0].stack)
                self.assertIsNone(battle._visible_frame_costs)
                self.assertIsNone(battle._local_frame_stages)


if __name__ == '__main__':
    unittest.main()
