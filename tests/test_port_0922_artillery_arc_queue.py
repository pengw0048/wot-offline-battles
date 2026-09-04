import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' /
    'gui' / 'mods' / 'offline_lan_0922' / 'artillery_arc_queue.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'port_0922_artillery_arc_queue_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArtilleryArcQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_module()

    @staticmethod
    def candidate(name, points):
        return {'name': name, 'path': tuple(points)}

    def test_actual_probe_calls_never_exceed_frame_budget(self):
        queue = self.runtime.ArcProbeQueue()
        candidate = self.candidate(
            'low', [(float(index), 2.0, 0.0) for index in range(11)])
        calls = []
        queue.request('shot', (candidate,), (10.0, 2.0, 0.0), 1.0)

        for now, expected in ((1.0, 4), (1.1, 4), (1.2, 2)):
            before = len(calls)
            used = queue.advance(
                now, 4, lambda first, second: calls.append(
                    (first, second)))
            self.assertEqual(expected, used)
            self.assertEqual(used, len(calls) - before)

        self.assertEqual((True, candidate), queue.result('shot', 1.2))

    def test_jobs_rotate_fairly_one_chord_at_a_time(self):
        queue = self.runtime.ArcProbeQueue()
        first = self.candidate('first', ((10, 0, 0), (11, 0, 0),
                                          (12, 0, 0)))
        second = self.candidate('second', ((20, 0, 0), (21, 0, 0),
                                            (22, 0, 0)))
        observed = []
        queue.request('first', (first,), (12, 0, 0), 2.0)
        queue.request('second', (second,), (22, 0, 0), 2.0)

        used = queue.advance(
            2.0, 4, lambda start, _end: observed.append(start) or None)

        self.assertEqual(4, used)
        self.assertEqual([10, 20, 11, 21], [point[0] for point in observed])
        self.assertEqual((True, first), queue.result('first', 2.0))
        self.assertEqual((True, second), queue.result('second', 2.0))

    def test_candidate_order_is_exactly_caller_selected(self):
        queue = self.runtime.ArcProbeQueue()
        high = self.candidate('high', ((0, 5, 0), (1, 6, 0), (2, 5, 0)))
        low = self.candidate('low', ((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        queue.request('shot', (high, low), (2, 5, 0), 3.0)

        queue.advance(3.0, 8, lambda _start, _end: None)

        self.assertEqual((True, high), queue.result('shot', 3.0))

    def test_blocked_first_candidate_rotates_to_the_next_candidate(self):
        queue = self.runtime.ArcProbeQueue()
        low = self.candidate('low', ((0, 0, 0), (1, 0, 0)))
        high = self.candidate('high', ((0, 5, 0), (1, 5, 0)))
        queue.request('shot', (low, high), (1, 5, 0), 4.0)

        queue.advance(
            4.0, 2,
            lambda start, _end: ((0.5, 0.0, 20.0)
                                 if start[1] == 0 else None))

        self.assertEqual((True, high), queue.result('shot', 4.0))

    def test_clear_is_not_published_until_final_chord_is_probed(self):
        queue = self.runtime.ArcProbeQueue()
        candidate = self.candidate(
            'low', ((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        queue.request('shot', (candidate,), (2, 0, 0), 5.0)

        self.assertEqual(1, queue.advance(5.0, 1, lambda _a, _b: None))
        self.assertEqual((False, None), queue.result('shot', 5.0))
        self.assertTrue(queue.is_pending('shot', 5.0))

        self.assertEqual(1, queue.advance(5.1, 1, lambda _a, _b: None))
        self.assertEqual((True, candidate), queue.result('shot', 5.1))

    def test_target_terrain_hit_accepts_only_the_probed_terminal_chord(self):
        queue = self.runtime.ArcProbeQueue(target_slop=3.0)
        candidate = self.candidate(
            'low', ((0, 0, 0), (10, 0, 0), (20, 0, 0)))
        queue.request('shot', (candidate,), (20, 0, 0), 6.0)

        self.assertEqual(2, queue.advance(
            6.0, 4, lambda start, _end: (
                (19.0, 0.0, 0.0) if start[0] == 10 else None)))

        self.assertEqual((True, candidate), queue.result('shot', 6.0))

    def test_early_chord_near_target_is_not_a_terminal_arrival(self):
        queue = self.runtime.ArcProbeQueue(target_slop=3.0)
        candidate = self.candidate(
            'low', ((0, 0, 0), (10, 0, 0), (20, 0, 0)))
        queue.request('shot', (candidate,), (20, 0, 0), 6.5)

        self.assertEqual(1, queue.advance(
            6.5, 1, lambda _start, _end: (18.0, 0.0, 0.0)))

        self.assertEqual((True, None), queue.result('shot', 6.5))

    def test_full_active_set_promotes_waiters_in_fifo_order(self):
        queue = self.runtime.ArcProbeQueue(max_jobs=2)
        candidate = self.candidate('low', ((0, 0, 0), (1, 0, 0)))
        keys = ['job-%d' % value for value in range(6)]
        for key in keys:
            queue.request(key, (candidate,), (1, 0, 0), 6.75)

        for frame in range(3):
            queue.advance(6.75 + frame * 0.01, 2, lambda _a, _b: None)

        self.assertEqual(
            [(True, candidate)] * len(keys),
            [queue.result(key, 6.78) for key in keys])

    def test_deferred_and_pending_work_are_never_clear(self):
        queue = self.runtime.ArcProbeQueue(max_jobs=1)
        candidate = self.candidate('low', ((0, 0, 0), (1, 0, 0)))
        self.assertEqual(
            (False, None),
            queue.request('first', (candidate,), (1, 0, 0), 7.0))

        self.assertEqual(
            (False, None),
            queue.request('deferred', (candidate,), (1, 0, 0), 7.0))
        self.assertEqual((False, None), queue.result('deferred', 7.0))
        self.assertTrue(queue.is_pending('deferred', 7.0))
        self.assertEqual((False, None), queue.result('first', 7.0))

    def test_success_and_failure_ttl_and_pose_key_invalidation(self):
        queue = self.runtime.ArcProbeQueue(
            success_ttl=1.0, failure_ttl=0.5)
        candidate = self.candidate('low', ((0, 0, 0), (1, 0, 0)))
        old_pose_key = ('bot', 'target', 10, 20)
        new_pose_key = ('bot', 'target', 11, 20)
        queue.request(old_pose_key, (candidate,), (1, 0, 0), 8.0)
        queue.advance(8.0, 1, lambda _a, _b: None)

        self.assertEqual((False, None), queue.result(new_pose_key, 8.1))
        self.assertEqual((True, candidate), queue.result(old_pose_key, 8.9))
        self.assertEqual((False, None), queue.result(old_pose_key, 9.0))

        self.assertEqual(
            (True, None), queue.request('no-root', (), (0, 0, 0), 10.0))
        self.assertEqual((True, None), queue.result('no-root', 10.49))
        self.assertEqual((False, None), queue.result('no-root', 10.5))

    def test_probe_error_and_job_timeout_fail_closed(self):
        queue = self.runtime.ArcProbeQueue(
            failure_ttl=0.5, max_job_age=0.25)
        candidate = self.candidate(
            'low', ((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        queue.request('error', (candidate,), (2, 0, 0), 11.0)

        def broken_probe(_first, _second):
            raise RuntimeError('native ray failed')

        self.assertEqual(1, queue.advance(11.0, 4, broken_probe))
        self.assertEqual((True, None), queue.result('error', 11.0))

        queue.request('expired', (candidate,), (2, 0, 0), 12.0)
        self.assertEqual((True, None), queue.result('expired', 12.251))

    def test_reset_clears_pending_and_cached_state(self):
        queue = self.runtime.ArcProbeQueue()
        candidate = self.candidate(
            'low', ((0, 0, 0), (1, 0, 0), (2, 0, 0)))
        queue.request('pending', (candidate,), (2, 0, 0), 13.0)
        queue.request('failure', (), (0, 0, 0), 13.0)

        queue.reset()

        self.assertEqual((False, None), queue.result('pending', 13.0))
        self.assertEqual((False, None), queue.result('failure', 13.0))
        self.assertFalse(queue.is_pending('pending', 13.0))
        self.assertEqual(
            {'pending': 0, 'waiting': 0, 'results': 0},
            queue.diagnostics())


if __name__ == '__main__':
    unittest.main()
