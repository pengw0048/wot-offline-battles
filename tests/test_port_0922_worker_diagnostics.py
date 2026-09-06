import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATH = (ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922' /
        'worker_diagnostics.py')
spec = importlib.util.spec_from_file_location('worker_diagnostic_test', PATH)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class WorkerCombatDiagnosticsTests(unittest.TestCase):
    def test_nested_stages_separate_total_and_self_time(self):
        clock = iter((1.0, 1.010, 1.030, 1.050))
        trace = diagnostics.WorkerCombatDiagnostics(lambda: next(clock))
        trace.begin_frame(1, 20.0, 'projectiles')
        parent = trace.start()
        child = trace.start()
        trace.stop('native.vehicle', child)
        trace.stop('projectile.vehicle', parent)
        row = trace.finish_frame()
        self.assertEqual({'calls': 1, 'total_ms': 50.0,
                          'self_ms': 30.0, 'max_ms': 50.0},
                         row['stages']['projectile.vehicle'])
        self.assertEqual(20.0, row['stages']['native.vehicle']['self_ms'])

    def test_combat_windows_start_after_lobby_and_obey_time_and_count_caps(self):
        reads = []
        trace = diagnostics.WorkerCombatDiagnostics(
            lambda: reads.append(True) or 0.0,
            capture_seconds=2.0, cooldown_seconds=3.0, maximum_captures=2)
        self.assertFalse(trace.begin_frame(1, 200.0))
        self.assertIsNone(trace.start())
        self.assertEqual([], reads)
        self.assertTrue(trace.begin_frame(2, 205.0, 'projectiles'))
        trace.finish_frame()
        self.assertTrue(trace.begin_frame(3, 206.0))
        trace.finish_frame()
        self.assertFalse(trace.begin_frame(4, 207.0, 'slow'))
        self.assertEqual('deadline', trace.drain_completed()[0]['end_reason'])
        self.assertFalse(trace.begin_frame(5, 209.0, 'slow'))
        self.assertTrue(trace.begin_frame(6, 210.0, 'queue'))
        trace.finish_frame()
        self.assertFalse(trace.begin_frame(7, 212.0, 'slow'))
        self.assertFalse(trace.begin_frame(8, 300.0, 'slow'))
        self.assertEqual(2, trace.capture)
        trace.reset()
        self.assertTrue(trace.begin_frame(1, 400.0, 'projectiles'))
        self.assertEqual(1, trace.capture)

    def test_wait_measures_each_identity_even_when_enqueued_before_capture(self):
        trace = diagnostics.WorkerCombatDiagnostics(lambda: 0.0)
        ordinary = (1, 'bot', 8)
        selected = (2, 'bot', 8)
        trace.queue_added(ordinary, 10.0, 10.5)
        trace.queue_added(selected, 11.0, 11.5)
        trace.begin_frame(1, 12.0, 'queue')
        trace.queue_state(12.0, (ordinary, selected), {selected})
        trace.queue_retired(selected, 12.0, 'probe', selected=True)
        trace.queue_retired(ordinary, 12.0, 'cache')
        row = trace.finish_frame()
        self.assertEqual(2000.0, row['queue']['oldest_wait_ms'])
        self.assertEqual(1500.0, row['queue']['oldest_due_ms'])
        self.assertEqual(500.0, row['queue']['selected_oldest_due_ms'])
        self.assertEqual(1500.0, row['completed_wait']['p50_ms'])
        self.assertEqual(1000.0,
                         row['selected_completed_wait']['p50_ms'])
        self.assertEqual({}, trace._queue)

        trace.close()
        capture = trace.drain_completed()[0]
        self.assertEqual(1, capture['frames'])
        self.assertEqual(12.0, capture['authority_start'])
        self.assertEqual(12.0, capture['authority_last_frame'])
        self.assertEqual(2000.0, capture['queue_maxima']['oldest_wait_ms'])
        self.assertEqual(ordinary, row['queue']['oldest_job'])
        self.assertEqual(selected, row['queue']['selected_oldest_job'])

    def test_receipt_counts_distinguish_publications_and_distinct_results(self):
        trace = diagnostics.WorkerCombatDiagnostics(lambda: 0.0)
        key = (1, 'bot', 2)
        trace.begin_frame(1, 10.0, 'queue')
        trace.receipt_stored(key, 10.0, True)
        trace.receipt_stored(key, 10.1, True)
        trace.receipt_published(key, 10.1, False)
        trace.receipt_published(key, 10.1, True)
        trace.receipt_stored(key, 10.2, False)
        counts = trace.finish_frame()['counts']
        self.assertEqual(1, counts['lane_positive_replaced_unpublished'])
        self.assertEqual(2, counts['lane_positive_publications'])
        self.assertEqual(1, counts['lane_distinct_positive_published'])
        self.assertEqual(1, counts['lane_selected_positive_publications'])

    def test_decorator_keeps_results_and_original_exceptions_on_clock_failure(self):
        def broken_clock():
            raise RuntimeError('clock failed')
        class Owner:
            @diagnostics.timed('owned')
            def call(self, fail=False):
                if fail:
                    raise ValueError('operation failed')
                return self
        owner = Owner()
        trace = diagnostics.WorkerCombatDiagnostics(broken_clock)
        owner._combat_diagnostics = trace
        trace.begin_frame(1, 1.0, 'queue')
        self.assertIs(owner, owner.call())
        self.assertFalse(trace.enabled)
        with self.assertRaisesRegex(ValueError, 'operation failed'):
            owner.call(fail=True)

    def test_exception_unwinds_scopes_and_round_end_flushes_partial_capture(self):
        class Owner:
            @diagnostics.timed('owned')
            def call(self):
                raise ValueError('native query failed')
        owner = Owner()
        trace = diagnostics.WorkerCombatDiagnostics(lambda: 1.0)
        owner._combat_diagnostics = trace
        trace.begin_frame(1, 1.0, 'queue')
        with self.assertRaises(ValueError):
            owner.call()
        self.assertEqual(1, trace.finish_frame()['stages']['owned']['calls'])
        trace.close()
        trace.close()
        completed = trace.drain_completed()
        self.assertEqual(1, len(completed))
        self.assertEqual('round_end', completed[0]['end_reason'])

    def test_queue_tracking_and_percentile_storage_are_bounded(self):
        trace = diagnostics.WorkerCombatDiagnostics(lambda: 0.0)
        trace.begin_frame(1, 1.0, 'queue')
        for index in range(2048):
            trace.queue_added((index, 'bot', 9), 0.0, 0.0)
        self.assertEqual(diagnostics.MAX_QUEUE_IDENTITIES, len(trace._queue))
        for index in range(1024):
            trace.queue_retired((index, 'bot', 9), 1.0, 'probe')
        trace.finish_frame()
        self.assertEqual(diagnostics.MAX_WAIT_SAMPLES,
                         len(trace._capture_waits))
        trace.close()
        row = trace.drain_completed()[0]
        self.assertEqual(1024, row['completed_wait']['count'])
        self.assertEqual(512, row['completed_wait']['kept'])
        self.assertEqual({}, trace._queue)


if __name__ == '__main__':
    unittest.main()
