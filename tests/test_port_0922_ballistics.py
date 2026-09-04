import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' /
    'gui' / 'mods' / 'offline_lan_0922' / 'ballistics.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'port_0922_ballistics_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BallisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ballistics = _load_module()

    def test_low_and_high_roots_land_on_target_in_time_order(self):
        solutions = self.ballistics.ballistic_solutions(
            (0.0, 2.0, 0.0), (100.0, 2.0, 0.0),
            100.0, 10.0, -math.pi * 0.5, 0.2)

        self.assertEqual(2, len(solutions))
        self.assertLess(solutions[0][1], solutions[1][1])
        self.assertGreater(solutions[0][0], solutions[1][0])
        for pitch, flight_time in solutions:
            point = self.ballistics.ballistic_position(
                (0.0, 2.0, 0.0), math.pi * 0.5,
                pitch, 100.0, 10.0, flight_time)
            self.assertAlmostEqual(100.0, point[0], places=5)
            self.assertAlmostEqual(2.0, point[1], places=5)
            self.assertAlmostEqual(0.0, point[2], places=5)

    def test_unreachable_target_and_invalid_scalar_have_no_solution(self):
        self.assertEqual((), self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (1000.0, 500.0, 0.0), 10.0, 10.0))
        self.assertEqual((), self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), 1.0, 10.0))
        self.assertEqual((), self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), 100.0, 0.0))

    def test_installed_pitch_limits_filter_the_high_root(self):
        solutions = self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
            100.0, 10.0, -0.2, 0.2)

        self.assertEqual(1, len(solutions))
        self.assertGreaterEqual(solutions[0][0], -0.2)
        self.assertLessEqual(solutions[0][0], 0.2)
        self.assertEqual((), self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
            100.0, 10.0, 0.0, 0.2))

    def test_negative_pitch_elevates_and_gravity_sign_is_a_magnitude(self):
        elevated = self.ballistics.ballistic_position(
            (0.0, 0.0, 0.0), 0.0, -0.2, 100.0, 10.0, 0.5)
        depressed = self.ballistics.ballistic_position(
            (0.0, 0.0, 0.0), 0.0, 0.2, 100.0, 10.0, 0.5)
        negative_gravity = self.ballistics.ballistic_position(
            (0.0, 0.0, 0.0), 0.0, -0.2, 100.0, -10.0, 0.5)

        self.assertGreater(elevated[1], 0.0)
        self.assertLess(depressed[1], 0.0)
        self.assertEqual(elevated, negative_gravity)
        self.assertEqual(
            self.ballistics.ballistic_solutions(
                (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), 100.0, 10.0),
            self.ballistics.ballistic_solutions(
                (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), 100.0, -10.0))

    def test_iterative_intercept_meets_moving_target(self):
        solution = self.ballistics.ballistic_intercept(
            (0.0, 2.0, 0.0), (0.0, 2.0, 300.0),
            (12.0, 0.0, 0.0), 300.0, 30.0,
            -1.4, 0.2)

        self.assertIsNotNone(solution)
        aim, pitch, flight_time = solution
        yaw = math.atan2(aim[0], aim[2])
        arrival = self.ballistics.ballistic_position(
            (0.0, 2.0, 0.0), yaw, pitch,
            300.0, 30.0, flight_time)
        moving_target = (12.0 * flight_time, 2.0, 300.0)

        self.assertGreater(aim[0], 10.0)
        for actual, expected in zip(arrival, moving_target):
            self.assertAlmostEqual(expected, actual, places=4)

    def test_iterative_intercept_returns_none_when_arc_has_no_root(self):
        self.assertIsNone(self.ballistics.ballistic_intercept(
            (0.0, 0.0, 0.0), (1000.0, 500.0, 0.0),
            (10.0, 0.0, 0.0), 10.0, 10.0))

    def test_protocol_lifetime_rejects_longer_arc_without_clamped_lead(self):
        self.assertIsNone(self.ballistics.ballistic_intercept(
            (0.0, 0.0, 0.0), (0.0, 0.0, 100.0),
            (1.0, 0.0, 0.0), 10.0, 0.1,
            -math.pi * 0.5, 0.2, max_lead_time=1.0))

        self.assertEqual((), self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (0.0, 0.0, 100.0),
            5.0, 0.01, -math.pi * 0.5, 0.2))

    def test_path_samples_exact_parabola_with_bounded_time_chords(self):
        pitch, flight_time = self.ballistics.ballistic_solutions(
            (0.0, 0.0, 0.0), (0.0, 0.0, 100.0),
            100.0, 10.0, -math.pi * 0.5, 0.2)[0]
        maximum_step = 0.1
        path = self.ballistics.ballistic_path(
            (0.0, 0.0, 0.0), 0.0, pitch, 100.0, 10.0,
            flight_time, maximum_step)
        actual_step = flight_time / float(len(path) - 1)

        self.assertLessEqual(actual_step, maximum_step + 1e-12)
        self.assertEqual((0.0, 0.0, 0.0), path[0])
        self.assertGreater(max(point[1] for point in path), 1.0)
        self.assertAlmostEqual(100.0, path[-1][2], places=5)
        self.assertAlmostEqual(0.0, path[-1][1], places=5)
        for index, point in enumerate(path):
            expected = self.ballistics.ballistic_position(
                (0.0, 0.0, 0.0), 0.0, pitch, 100.0, 10.0,
                flight_time * float(index) / float(len(path) - 1))
            self.assertEqual(expected, point)


if __name__ == '__main__':
    unittest.main()
