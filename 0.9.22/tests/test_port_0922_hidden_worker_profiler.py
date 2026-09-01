import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922.hidden_worker_profiler import \
    HiddenWorkerProfiler


class HiddenWorkerProfilerTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
