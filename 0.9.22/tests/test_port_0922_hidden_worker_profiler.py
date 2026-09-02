import pathlib
import sys
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922.hidden_worker_profiler import (
    BOT_UPDATE_RESIDUAL_SEMANTICS,
    MAX_WORK_COUNTER_VALUE,
    PYTHON_CATEGORY_SCOPES,
    HiddenWorkerProfiler,
)
from gui.mods.offline_lan_0922 import hidden_worker_profiler


class HiddenWorkerProfilerTests(unittest.TestCase):
    @staticmethod
    def _incrementing_clock():
        state = {'value': 0.0}
        lock = threading.Lock()

        def clock():
            with lock:
                state['value'] += 0.001
                return state['value']

        return clock

    def test_inactive_profiler_invokes_original_once_without_reading_clock(self):
        clock_calls = []
        calls = []
        profiler = HiddenWorkerProfiler(
            clock=lambda: clock_calls.append(True))

        value = profiler.native_call(
            'spotting', 'wg_collideSegment',
            lambda argument: calls.append(argument) or 7, 3)

        self.assertEqual(7, value)
        self.assertEqual([3], calls)
        self.assertEqual([], clock_calls)
        self.assertFalse(profiler.end_frame()['measured'])

        self.assertFalse(profiler.begin_frame(False))
        self.assertFalse(profiler.begin_frame(False))
        self.assertEqual([], clock_calls)

    def test_native_calls_are_counted_by_category_api_time_and_failure(self):
        clock = iter((1.0, 1.004, 2.0, 2.006))
        calls = []
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)

        self.assertEqual('hit', profiler.native_call(
            'spotting', 'wg_collideSegment',
            lambda: calls.append('first') or 'hit'))
        with self.assertRaisesRegex(RuntimeError, 'native failed'):
            profiler.native_call(
                'spotting', 'wg_collideSegment',
                lambda: (_ for _ in ()).throw(RuntimeError('native failed')))
        profile = profiler.end_frame()

        self.assertEqual(['first'], calls)
        self.assertTrue(profile['measured'])
        row = profile['native']['spotting.wg_collideSegment']
        self.assertEqual((2, 1), (row[0], row[2]))
        self.assertAlmostEqual(0.010, row[1])

    def test_start_clock_failure_preserves_original_call_semantics(self):
        def broken_clock():
            raise RuntimeError('diagnostic clock failed')

        calls = []
        profiler = HiddenWorkerProfiler(clock=broken_clock)
        profiler.begin_frame(True)

        value = profiler.native_call(
            'spotting', 'wg_collideSegment',
            lambda: calls.append('return') or 17)
        self.assertEqual(17, value)
        with self.assertRaisesRegex(ValueError, 'original failed'):
            profiler.native_call(
                'spotting', 'wg_collideSegment',
                lambda: (calls.append('raise'),
                         (_ for _ in ()).throw(
                             ValueError('original failed')))[1])

        self.assertEqual(['return', 'raise'], calls)

    def test_scheduled_calls_stay_separate_from_the_next_render_callback(self):
        clock = iter((1.0, 1.003, 2.0, 2.005, 3.0, 3.0))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)
        profiler.native_call(
            'ground', 'wg_collideSegment', lambda: None)
        first = profiler.end_frame()
        profiler.native_call(
            'destructible_state', 'wg_getDestructibleMatrix', lambda: None)
        interval = profiler.begin_frame(True)
        marker = profiler.python_started('foliage_dynamic')
        profiler.python_finished(marker)
        second = profiler.end_frame()

        first_row = first['native']['ground.wg_collideSegment']
        self.assertEqual((1, 0), (first_row[0], first_row[2]))
        self.assertAlmostEqual(0.003, first_row[1])
        second_row = interval['offframe_native'][
            'destructible_state.wg_getDestructibleMatrix']
        self.assertEqual((1, 0), (second_row[0], second_row[2]))
        self.assertAlmostEqual(0.005, second_row[1])
        self.assertEqual({}, second['offframe_native'])
        self.assertEqual((1, 0.0, 0), second['python']['foliage_dynamic'])

    def test_unknown_categories_are_bounded_to_other(self):
        clock = iter((1.0, 1.001))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)

        profiler.native_call('attacker supplied category', 'x' * 80,
                             lambda: None)
        profile = profiler.end_frame()

        self.assertEqual(['other.native'], list(profile['native']))
        row = profile['native']['other.native']
        self.assertEqual((1, 0), (row[0], row[2]))
        self.assertAlmostEqual(0.001, row[1])

    def test_ram_is_a_first_class_category(self):
        clock = iter((1.0, 1.002))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)

        profiler.native_call(
            'ram', 'hitTester.localHitTest', lambda: ())
        profile = profiler.end_frame()

        self.assertIn('ram.hitTester.localHitTest', profile['native'])
        self.assertNotIn('other.hitTester.localHitTest', profile['native'])

    def test_category_context_routes_only_the_nested_native_boundary(self):
        clock = iter((1.0, 1.003))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)

        def python_vehicle_wrapper():
            return profiler.native_call(
                profiler.current_category('projectile_vehicle'),
                'hitTester.localHitTest', lambda: ('hit',))

        result = profiler.category_call(
            'firing_lane', python_vehicle_wrapper)
        profile = profiler.end_frame()

        self.assertEqual(('hit',), result)
        self.assertEqual(
            ['firing_lane.hitTester.localHitTest'],
            list(profile['native']))

    def test_slow_trace_preserves_context_result_and_filter_semantics(self):
        clock = iter((1.0, 1.002, 2.0, 2.002))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True, context={
            'map': '05_prohorovka',
            'round': 'round-7',
            'destructible_revision': 11,
            'foliage_revision': 4,
        })
        result = ((4.0, 5.0, 6.0), (0.0, 1.0, 0.0), 37)

        self.assertEqual(result, profiler.native_call(
            'spotting', 'wg_collideSegment', lambda *unused: result,
            9, (1.0, 2.0, 3.0), (7.0, 8.0, 9.0), 128))
        profiler.native_call(
            'ground', 'wg_collideSegment', lambda *unused: None,
            9, (1.0, 3.0, 3.0), (1.0, -3.0, 3.0), 128,
            lambda unused: True)
        profile = profiler.end_frame()

        self.assertEqual(1, profile['frame_ordinal'])
        self.assertEqual(2, len(profile['trace']))
        trace = next(row for row in profile['trace']
                     if row['category'] == 'spotting')
        self.assertEqual('05_prohorovka', trace['map'])
        self.assertEqual('round-7', trace['round'])
        self.assertEqual(11, trace['destructible_revision'])
        self.assertEqual(4, trace['foliage_revision'])
        self.assertEqual(1, trace['profiler_frame'])
        self.assertEqual(37, trace['result']['material'])
        self.assertTrue(trace['filter_free'])
        self.assertTrue(trace['replay_candidate'])
        self.assertFalse(trace['replayable'])
        self.assertFalse(trace['timing_includes_python_filter'])
        filtered = next(row for row in profile['trace']
                        if row['category'] == 'ground')
        self.assertTrue(filtered['filtered'])
        self.assertFalse(filtered['filter_free'])
        self.assertFalse(filtered['replay_candidate'])
        self.assertTrue(filtered['timing_includes_python_filter'])

    def test_representative_segment_sampling_hits_periodic_boundary(self):
        now = [0.0]

        def clock():
            now[0] += 0.0000001
            return now[0]

        profiler = HiddenWorkerProfiler(clock=clock)
        profiler.begin_frame(True, context={
            'destructible_revision': 2, 'foliage_revision': 3})
        for index in range(256):
            profiler.native_call(
                'spotting', 'wg_collideSegment', lambda *unused: None,
                1, (index, 0, 0), (index + 1, 0, 0), 128)
        profile = profiler.end_frame()

        samples = profile['representative_trace']
        self.assertEqual([1, 256], [
            row['sample_ordinal'] for row in samples])
        self.assertLessEqual(len(samples), 2)

    def test_alternates_full_timing_with_wrapper_only_baseline(self):
        now = [0.0]
        profiler = HiddenWorkerProfiler(
            clock=lambda: now[0], alternate_modes=True,
            full_timing_seconds=1.0, baseline_seconds=0.5)

        profiler.begin_frame(True)
        self.assertEqual('timing+bounded_trace', profiler.end_frame()['mode'])
        now[0] = 1.1
        profiler.begin_frame(True)
        baseline = profiler.end_frame()
        self.assertEqual('wrapper_only_baseline', baseline['mode'])
        self.assertFalse(baseline['measured'])
        now[0] = 1.7
        profiler.begin_frame(True)
        self.assertEqual('timing+bounded_trace', profiler.end_frame()['mode'])

    def test_phase_categories_publish_non_additive_scope_metadata(self):
        clock = iter((1.0, 1.002, 2.0, 2.003, 3.0, 3.004,
                      4.0, 4.005, 5.0, 5.006, 6.0, 6.007,
                      7.0, 7.008))
        profiler = HiddenWorkerProfiler(clock=lambda: next(clock))
        profiler.begin_frame(True)

        for category in (
                'bot_setup', 'bot_astar_inclusive',
                'bot_scheduler_catchup', 'projectile_step',
                'worker_message_validate', 'worker_message_send',
                'destructible_filter_prepare'):
            profiler.python_call(category, lambda: None)
        profile = profiler.end_frame()

        self.assertNotIn('other', profile['python'])
        self.assertEqual(
            'bot_step_exclusive',
            profile['python_category_scopes']['bot_setup'])
        self.assertEqual(
            'nested_inclusive',
            profile['python_category_scopes']['bot_astar_inclusive'])
        self.assertEqual(
            'scheduler_inclusive',
            profile['python_category_scopes']['bot_scheduler_catchup'])
        self.assertEqual(
            'projectile_outer_inclusive',
            profile['python_category_scopes']['projectile_step'])
        self.assertEqual(
            'message_nested_inclusive',
            profile['python_category_scopes']['worker_message_send'])
        self.assertEqual(
            'message_nested_inclusive',
            profile['python_category_scopes']['worker_message_validate'])
        self.assertEqual(
            'nested_inclusive',
            profile['python_category_scopes']['destructible_filter_prepare'])
        self.assertEqual(
            BOT_UPDATE_RESIDUAL_SEMANTICS,
            profile['bot_update_residual_semantics'])
        self.assertIn('all_other_scopes_are_inclusive',
                      profile['python_scope_sum_rule'])
        self.assertEqual(PYTHON_CATEGORY_SCOPES,
                         profile['python_category_scopes'])

    def test_work_counters_are_bounded_validated_and_clock_free(self):
        clock_calls = []
        profiler = HiddenWorkerProfiler(
            clock=lambda: clock_calls.append(True) or 0.0)
        profiler.begin_frame(True)

        self.assertTrue(profiler.work_add('bot_live_rows', 2))
        self.assertTrue(profiler.work_add('bot_live_rows', 0.5))
        self.assertTrue(profiler.work_add(
            'bot_motion_commit_sweeps', MAX_WORK_COUNTER_VALUE))
        self.assertTrue(profiler.work_add('bot_motion_commit_sweeps', 1))
        self.assertTrue(profiler.work_add('worker_queue_depth', 4))
        self.assertTrue(profiler.work_add('worker_queue_depth', 2))
        self.assertTrue(profiler.work_add('worker_queue_depth', 7))
        self.assertTrue(profiler.work_add(
            'bot_shot_lane_identity_depth', 420))
        self.assertTrue(profiler.work_add(
            'bot_shot_lane_identity_depth', 84))
        self.assertTrue(profiler.work_add(
            'bot_shot_lane_materialized', 16))
        self.assertTrue(profiler.work_add(
            'destructible_filter_preparations', 9))
        self.assertFalse(profiler.work_add('unbounded-key', 1))
        self.assertFalse(profiler.work_add('bot_rows', True))
        self.assertFalse(profiler.work_add('bot_rows', -1))
        self.assertFalse(profiler.work_add('bot_rows', float('nan')))
        self.assertFalse(profiler.work_add('bot_rows', float('inf')))
        self.assertFalse(profiler.work_add('bot_rows', '3'))
        profile = profiler.end_frame()

        self.assertEqual([], clock_calls)
        self.assertEqual(2.5, profile['work']['bot_live_rows'])
        self.assertEqual(
            MAX_WORK_COUNTER_VALUE,
            profile['work']['bot_motion_commit_sweeps'])
        self.assertEqual(7, profile['work']['worker_queue_depth'])
        self.assertEqual(
            420, profile['work']['bot_shot_lane_identity_depth'])
        self.assertEqual(16, profile['work']['bot_shot_lane_materialized'])
        self.assertEqual(
            9, profile['work']['destructible_filter_preparations'])
        self.assertNotIn('unbounded-key', profile['work'])
        self.assertNotIn('bot_rows', profile['work'])
        self.assertTrue(profile['work_counters_recorded'])
        self.assertEqual(
            'sampled_max',
            profile['work_counter_aggregations']['worker_queue_depth'])
        self.assertEqual(
            'sampled_max', profile['work_counter_aggregations'][
                'bot_shot_lane_identity_depth'])
        self.assertIn(
            'not_native_ray_count',
            profile['work_counter_notes']['bot_motion_commit_sweeps'])
        self.assertTrue(callable(hidden_worker_profiler.work_add))

    def test_wrapper_only_baseline_keeps_work_without_timing(self):
        now = [0.0]
        profiler = HiddenWorkerProfiler(
            clock=lambda: now[0], alternate_modes=True,
            full_timing_seconds=1.0, baseline_seconds=5.0)
        profiler.begin_frame(True)
        profiler.end_frame()
        now[0] = 1.1
        profiler.begin_frame(True)

        self.assertTrue(profiler.work_add('projectile_segments', 11))
        marker = profiler.python_started('projectile_step')
        profile = profiler.end_frame()

        self.assertIsNone(marker)
        self.assertFalse(profile['measured'])
        self.assertEqual('wrapper_only_baseline', profile['mode'])
        self.assertEqual(11, profile['work']['projectile_segments'])
        self.assertEqual({}, profile['python'])
        self.assertEqual(0, profile['clock_reads'])
        self.assertTrue(profile['work_counters_recorded'])

    def test_inactive_and_visible_sessions_reject_work(self):
        clock_calls = []
        profiler = HiddenWorkerProfiler(
            clock=lambda: clock_calls.append(True) or 0.0)

        self.assertFalse(profiler.work_add('bot_rows'))
        self.assertFalse(profiler.begin_frame(False))
        self.assertFalse(profiler.work_add('bot_rows'))
        self.assertEqual([], clock_calls)
        self.assertFalse(profiler.end_frame()['work_counters_recorded'])

    def test_render_thread_offframe_work_is_detached_next_interval(self):
        profiler = HiddenWorkerProfiler(clock=lambda: 0.0)
        profiler.begin_frame(True)
        profiler.end_frame()

        self.assertTrue(profiler.work_add('worker_wire_bytes', 128))
        interval = profiler.begin_frame(True)

        self.assertEqual(128, interval['offframe_work']['worker_wire_bytes'])
        self.assertEqual({}, interval['work'])
        self.assertEqual(
            'active_hidden_worker_session_all_modes',
            interval['work_counter_scope'])

    def test_background_python_marker_is_never_charged_to_render_frame(self):
        profiler = HiddenWorkerProfiler(clock=self._incrementing_clock())
        profiler.begin_frame(True)
        completed = []

        def run_background_send():
            marker = profiler.python_started('worker_message_send')
            profiler.work_add('worker_wire_bytes', 64)
            completed.append(profiler.python_finished(marker))

        thread = threading.Thread(target=run_background_send)
        thread.start()
        thread.join()
        frame = profiler.end_frame()
        interval = profiler.begin_frame(True)

        self.assertEqual([True], completed)
        self.assertEqual({}, frame['python'])
        self.assertEqual({}, frame['work'])
        self.assertEqual(
            1, interval['offframe_python']['worker_message_send'][0])
        self.assertEqual(64, interval['offframe_work']['worker_wire_bytes'])
        self.assertTrue(interval['offframe_python_measured'])
        self.assertEqual(
            'begin_frame_thread_is_render;other_threads_are_offframe',
            interval['python_thread_attribution'])

    def test_background_marker_crossing_interval_swap_is_not_lost(self):
        profiler = HiddenWorkerProfiler(clock=self._incrementing_clock())
        profiler.begin_frame(True)
        marker_started = threading.Event()
        allow_finish = threading.Event()
        completed = []

        def run_background_send():
            marker = profiler.python_started('worker_message_send')
            marker_started.set()
            allow_finish.wait(2.0)
            completed.append(profiler.python_finished(marker))

        thread = threading.Thread(target=run_background_send)
        thread.start()
        self.assertTrue(marker_started.wait(2.0))
        profiler.end_frame()
        first_interval = profiler.begin_frame(True)
        allow_finish.set()
        thread.join(2.0)
        self.assertFalse(thread.is_alive())
        profiler.end_frame()
        second_interval = profiler.begin_frame(True)

        self.assertEqual([True], completed)
        self.assertNotIn(
            'worker_message_send', first_interval.get('offframe_python', {}))
        self.assertEqual(
            1, second_interval['offframe_python']['worker_message_send'][0])
        self.assertEqual(
            'timing_at_marker_start;reported_in_completion_interval',
            second_interval['offframe_python_timing_semantics'])


if __name__ == '__main__':
    unittest.main()
