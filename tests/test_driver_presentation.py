import math
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              'src', 'res', 'scripts', 'client', 'gui',
                              'mods', 'offline_lan_0922'))
from driver_presentation import DriverPresentation


def pose(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0):
    return {'position': [x, y, z], 'yaw': yaw, 'pitch': pitch, 'roll': roll}


class DriverPresentationTests(unittest.TestCase):
    def setUp(self):
        self.timeline = DriverPresentation()

    def moving(self):
        self.assertTrue(self.timeline.accept(pose(), 1000000, 4.0))
        self.assertTrue(self.timeline.accept(
            pose(10.0, 2.0, -4.0, yaw=0.2, pitch=0.3, roll=-0.4),
            1200000, 4.5))

    def test_empty_and_reset_forget_every_previous_round_pose(self):
        self.assertIsNone(self.timeline.candidate(1.0))
        self.assertFalse(self.timeline.commit_display(pose()))
        self.moving()
        self.timeline.commit_display(self.timeline.candidate(4.6))
        self.timeline.reset()
        self.assertIsNone(self.timeline.canonical)
        self.assertIsNone(self.timeline.last_arrival_time)
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertIsNone(self.timeline.candidate(5.0))
        self.assertTrue(self.timeline.accept(pose(3), 1, 5.0))
        self.assertEqual(self.timeline.candidate(5.01)['position'], (3, 0, 0))

    def test_first_and_zero_time_samples_never_predict(self):
        self.assertTrue(self.timeline.accept(pose(1), 0, 1.0))
        self.assertEqual(self.timeline.candidate(1.1)['position'], (1, 0, 0))
        self.assertTrue(self.timeline.accept(pose(2), 100000, 1.2))
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertEqual(self.timeline.candidate(1.25)['position'], (2, 0, 0))
        self.assertTrue(self.timeline.accept(pose(3), 200000, 1.3))
        self.assertAlmostEqual(self.timeline.source_interval, 0.1)
        self.assertTrue(self.timeline.accept(pose(4), 0, 1.4))
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertEqual(self.timeline.candidate(1.5)['position'], (4, 0, 0))

    def test_motion_uses_source_interval_not_arrival_interval(self):
        self.moving()
        value = self.timeline.candidate(4.6)
        for actual, expected in zip(value['position'], (15, 3, -6)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(value['yaw'], 0.3)
        self.assertEqual(value['pitch'], 0.3)
        self.assertEqual(value['roll'], -0.4)
        self.assertAlmostEqual(self.timeline.source_interval, 0.2)
        self.assertEqual(self.timeline.last_arrival_time, 4.5)

    def test_multiple_ordered_receipts_can_share_one_arrival_time(self):
        self.timeline.accept(pose(), 100000, 2.0)
        self.timeline.accept(pose(1), 200000, 2.0)
        self.assertAlmostEqual(self.timeline.candidate(2.05)['position'][0],
                               1.5)

    def test_every_candidate_is_based_on_canonical_not_display(self):
        self.moving()
        self.timeline.commit_display(pose(800, yaw=-2))
        for now, expected in ((4.55, 12.5), (4.6, 15), (4.65, 17.5)):
            value = self.timeline.candidate(now)
            self.assertAlmostEqual(value['position'][0], expected)
            self.timeline.commit_display(value)
        self.assertEqual(self.timeline.canonical['position'], (10, 2, -4))

    def test_candidate_queries_do_not_implicitly_commit_unguarded_motion(self):
        self.moving()
        self.assertGreater(self.timeline.candidate(4.6)['position'][0], 10)
        self.assertEqual(self.timeline.candidate(4.71)['position'], (10, 2, -4))
        self.assertEqual(self.timeline.source_interval, 0.0)

    def test_expiry_holds_last_guarded_pose_and_never_keeps_walking(self):
        self.moving()
        self.timeline.commit_display(self.timeline.candidate(4.6))
        safe = self.timeline.candidate(4.6, allow_continuation=False)
        self.assertEqual(self.timeline.candidate(4.71), safe)
        self.assertEqual(self.timeline.candidate(100.0), safe)

    def test_exact_source_horizon_is_allowed_but_cannot_be_exceeded(self):
        self.moving()
        horizon = self.timeline.last_arrival_time + self.timeline.source_interval
        value = self.timeline.candidate(horizon)
        self.assertAlmostEqual(value['position'][0], 20.0)
        self.timeline.commit_display(value)
        self.assertEqual(self.timeline.candidate(horizon + 0.01), value)
        self.assertEqual(self.timeline.source_interval, 0.0)

    def test_new_receipt_overrides_an_old_guarded_prediction(self):
        self.moving()
        self.timeline.commit_display(self.timeline.candidate(4.6))
        self.assertTrue(self.timeline.accept(pose(11), 1400000, 4.65))
        self.assertEqual(self.timeline.candidate(4.65), self.timeline.canonical)
        self.assertEqual(self.timeline.candidate(4.65, False)['position'],
                         (11, 0, 0))
        self.assertAlmostEqual(self.timeline.candidate(4.75)['position'][0],
                               11.5)

    def test_shortest_yaw_difference_crosses_both_sides_of_pi(self):
        for first, second, expected in ((179, -179, -178),
                                        (-179, 179, 178)):
            self.timeline.reset()
            self.timeline.accept(pose(yaw=math.radians(first)), 100000, 1.0)
            self.timeline.accept(pose(yaw=math.radians(second)), 200000, 1.1)
            self.assertAlmostEqual(self.timeline.candidate(1.15)['yaw'],
                                   math.radians(expected))

    def test_all_input_output_copies_leave_canonical_immutable(self):
        original = pose(1, yaw=0.4)
        self.timeline.accept(original, 100000, 1.0)
        original['position'][0] = 800
        original['yaw'] = 2.0
        canonical = self.timeline.canonical
        canonical['position'] = (900, 900, 900)
        candidate = self.timeline.candidate(1.0)
        candidate['yaw'] = 3.0
        committed = pose(2)
        self.timeline.commit_display(committed)
        committed['position'][0] = 1000
        self.assertEqual(self.timeline.canonical['position'], (1, 0, 0))
        self.assertEqual(self.timeline.canonical['yaw'], 0.4)
        self.assertEqual(self.timeline.candidate(1.0, False)['position'],
                         (2, 0, 0))

    def test_repeated_or_old_source_time_stops_without_rolling_back_canonical(self):
        for source_time in (1200000, 1100000):
            self.timeline.reset()
            self.moving()
            self.timeline.commit_display(self.timeline.candidate(4.6))
            safe = self.timeline.candidate(4.6, False)
            self.assertFalse(self.timeline.accept(pose(-1), source_time, 4.61))
            self.assertEqual(self.timeline.source_interval, 0.0)
            self.assertEqual(self.timeline.canonical['position'], (10, 2, -4))
            self.assertEqual(self.timeline.candidate(4.65), safe)

    def test_backward_arrival_time_rejects_the_sample_and_stops_prediction(self):
        self.moving()
        self.assertFalse(self.timeline.accept(pose(99), 1400000, 4.4))
        self.assertEqual(self.timeline.last_arrival_time, 4.5)
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertEqual(self.timeline.canonical['position'], (10, 2, -4))

    def test_backward_candidate_clock_holds_last_safe_pose(self):
        self.moving()
        self.timeline.commit_display(self.timeline.candidate(4.6))
        safe = self.timeline.candidate(4.6, False)
        self.assertEqual(self.timeline.candidate(4.55), safe)
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertEqual(self.timeline.candidate(4.65), safe)

    def test_candidate_before_arrival_does_not_predict_backwards(self):
        self.moving()
        self.assertEqual(self.timeline.candidate(4.4), self.timeline.canonical)
        self.assertEqual(self.timeline.source_interval, 0.0)

    def test_invalid_pose_and_timestamps_cannot_poison_valid_state(self):
        invalid = [({}, 1400000, 4.6), (pose(float('nan')), 1400000, 4.6),
                   (pose(yaw=float('inf')), 1400000, 4.6),
                   (pose(pitch=True), 1400000, 4.6), (pose(), -1, 4.6),
                   (pose(), True, 4.6), (pose(), 1.5, 4.6),
                   (pose(), 2 ** 63, 4.6), (pose(), 1400000, float('inf'))]
        for value, source_time, arrival in invalid:
            self.timeline.reset()
            self.moving()
            self.assertFalse(self.timeline.accept(value, source_time, arrival))
            self.assertEqual(self.timeline.source_interval, 0.0)
            self.assertEqual(self.timeline.candidate(4.65), self.timeline.canonical)

    def test_invalid_candidate_clock_or_commit_holds_safe_state(self):
        for bad_time in (float('nan'), float('inf'), None, True):
            self.timeline.reset()
            self.moving()
            self.assertEqual(self.timeline.candidate(bad_time), self.timeline.canonical)
            self.assertEqual(self.timeline.source_interval, 0.0)
        self.timeline.reset()
        self.moving()
        self.assertFalse(self.timeline.commit_display(pose(roll=float('nan'))))
        self.assertEqual(self.timeline.candidate(4.6), self.timeline.canonical)

    def test_finite_values_whose_difference_overflows_do_not_predict(self):
        self.timeline.accept(pose(-1e308), 100000, 1.0)
        self.assertTrue(self.timeline.accept(pose(1e308), 200000, 1.1))
        self.assertEqual(self.timeline.source_interval, 0.0)
        self.assertEqual(self.timeline.candidate(1.15), self.timeline.canonical)

    def test_overflowing_continuation_does_not_replace_last_safe_pose(self):
        self.timeline.accept(pose(1e308), 100000, 1.0)
        self.timeline.accept(pose(1.7e308), 200000, 1.1)
        self.assertEqual(self.timeline.candidate(1.2), self.timeline.canonical)
        self.assertEqual(self.timeline.source_interval, 0.0)


if __name__ == '__main__':
    unittest.main()
