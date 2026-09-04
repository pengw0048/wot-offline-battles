import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' /
    'gui' / 'mods' / 'offline_lan_0922' / 'projectile_runtime.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'port_0922_projectile_runtime_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_module()

    def test_trajectory_uses_signed_gravity_vector(self):
        position = self.runtime.trajectory_position(
            (1.0, 2.0, 3.0), (100.0, 10.0, -20.0),
            (0.0, -10.0, 2.0), 2.0)

        self.assertEqual((201.0, 2.0, -33.0), position)

    def test_high_arc_range_uses_frozen_start_not_travelled_path(self):
        state = {'start': (0.0, 0.0, 0.0), 'distance': 13.0}

        distance = self.runtime.projectile_range_distance(
            state, (6.0, 8.0, 0.0))

        self.assertEqual(10.0, distance)
        self.assertGreater(state['distance'], distance)

    def test_range_origin_in_payload_takes_precedence_over_muzzle_start(self):
        state = {
            'start': (100.0, 100.0, 100.0),
            'payload': {'range_origin': (1.0, 2.0, 3.0)},
        }

        distance = self.runtime.projectile_range_distance(
            state, (4.0, 6.0, 15.0))

        self.assertEqual(13.0, distance)

    def test_ideal_reflection_normalizes_normal_and_preserves_speed(self):
        incoming = (3.0, -4.0, 12.0)

        reflected = self.runtime.ideal_reflection_velocity(
            incoming, (0.0, 7.0, 0.0))

        self.assertEqual((3.0, 4.0, 12.0), reflected)
        incoming_speed = math.sqrt(sum(value * value for value in incoming))
        reflected_speed = math.sqrt(sum(
            value * value for value in reflected))
        self.assertAlmostEqual(incoming_speed, reflected_speed, places=12)

    def test_ideal_reflection_rejects_non_finite_or_degenerate_inputs(self):
        invalid_pairs = (
            ((1.0, 2.0), (0.0, 1.0, 0.0)),
            ((1.0, 2.0, float('nan')), (0.0, 1.0, 0.0)),
            ((1.0, 2.0, 3.0), (0.0, float('inf'), 0.0)),
            ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0)),
            ((1.0, 2.0, 3.0), (1.0e-13, 0.0, 0.0)),
        )

        for incoming, normal in invalid_pairs:
            with self.subTest(incoming=incoming, normal=normal):
                self.assertIsNone(self.runtime.ideal_reflection_velocity(
                    incoming, normal))

    def test_absolute_trajectory_and_substeps_are_frame_rate_invariant(self):
        final_positions = []
        for frames_per_second in (30, 60, 120):
            chords = []
            for frame in range(frames_per_second):
                start = float(frame) / float(frames_per_second)
                end = float(frame + 1) / float(frames_per_second)
                chords.extend(self.runtime.substep_boundaries(start, end))

            self.assertAlmostEqual(0.0, chords[0][0])
            self.assertAlmostEqual(1.0, chords[-1][1])
            self.assertAlmostEqual(
                1.0, sum(end - start for start, end in chords))
            self.assertTrue(all(
                0.0 < end - start <=
                self.runtime.PROJECTILE_MAX_SUBSTEP_SECONDS + 1e-9
                for start, end in chords))
            final_positions.append(self.runtime.trajectory_position(
                (3.0, 7.0, -2.0), (80.0, 15.0, 12.0),
                (0.0, -9.81, 0.0), chords[-1][1]))

        for position in final_positions:
            self.assertEqual(final_positions[0], position)

    def test_relative_sweep_hit_is_invariant_at_30_60_and_120_fps(self):
        # Projectile x=100t crosses target x=50+20t at t=0.625. The target hit
        # tester is available only in each rendered frame's current matrix.
        for frames_per_second in (30, 60, 120):
            hit = False
            frame_time = 1.0 / float(frames_per_second)
            for frame in range(frames_per_second):
                frame_start = float(frame) * frame_time
                frame_end = float(frame + 1) * frame_time
                target_previous = (50.0 + 20.0 * frame_start, 0.0, 0.0)
                target_current = (50.0 + 20.0 * frame_end, 0.0, 0.0)
                for start_time, end_time in self.runtime.substep_boundaries(
                        frame_start, frame_end):
                    interval_start = (
                        (start_time - frame_start) / frame_time)
                    interval_end = ((end_time - frame_start) / frame_time)
                    adjusted_start, adjusted_end = (
                        self.runtime.compensate_segment_for_moving_target(
                            (100.0 * start_time, 0.0, 0.0),
                            (100.0 * end_time, 0.0, 0.0),
                            target_previous, target_current,
                            interval_start, interval_end))
                    if self.runtime.point_segment_distance_sq(
                            target_current, adjusted_start,
                            adjusted_end) <= 1e-12:
                        hit = True
                        break
                if hit:
                    break
            self.assertTrue(hit, '%s FPS missed relative sweep' %
                            frames_per_second)

    def test_slow_frame_is_fully_covered_by_bounded_substeps(self):
        chords = list(self.runtime.substep_boundaries(0.10, 0.21, 0.025))

        self.assertEqual(0.10, chords[0][0])
        self.assertAlmostEqual(0.21, chords[-1][1])
        self.assertTrue(all(end > start for start, end in chords))
        self.assertTrue(all(
            end - start <= 0.0250001 for start, end in chords))
        self.assertAlmostEqual(
            0.11, sum(end - start for start, end in chords))

    def test_relative_sweep_crosses_target_in_current_collision_frame(self):
        adjusted_start, adjusted_end = (
            self.runtime.compensate_segment_for_moving_target(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                (5.0, 0.0, 0.0), (7.0, 0.0, 0.0)))

        self.assertEqual((2.0, 0.0, 0.0), adjusted_start)
        self.assertEqual((10.0, 0.0, 0.0), adjusted_end)
        fraction = ((7.0 - adjusted_start[0]) /
                    (adjusted_end[0] - adjusted_start[0]))
        self.assertAlmostEqual(0.625, fraction)
        self.assertAlmostEqual(6.25, 10.0 * fraction)
        self.assertAlmostEqual(6.25, 5.0 + 2.0 * fraction)

    def test_relative_sweep_honours_partial_frame_interval(self):
        adjusted_start, adjusted_end = (
            self.runtime.compensate_segment_for_moving_target(
                (20.0, 0.0, 0.0), (30.0, 0.0, 0.0),
                (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), 0.25, 0.75))

        self.assertEqual((26.0, 0.0, 0.0), adjusted_start)
        self.assertEqual((32.0, 0.0, 0.0), adjusted_end)

    def test_broadphase_distance_clamps_to_finite_segment(self):
        self.assertEqual(4.0, self.runtime.point_segment_distance_sq(
            (5.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(29.0, self.runtime.point_segment_distance_sq(
            (15.0, 2.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_b4_gravity_uses_50ms_below_five_centimetres_error(self):
        step = self.runtime.curvature_limited_substep(
            (0.0, -143.0, 0.0))

        self.assertEqual(0.05, step)
        error = self.runtime.parabolic_chord_error(143.0, step)
        self.assertAlmostEqual(0.0446875, error)
        self.assertLessEqual(
            error, self.runtime.PROJECTILE_MAX_CHORD_ERROR_METERS)

    def test_stock_1513_max_gravity_adapts_to_five_centimetres_error(self):
        step = self.runtime.curvature_limited_substep(
            (0.0, -190.0, 0.0))

        self.assertAlmostEqual(0.04588314677411235, step)
        self.assertAlmostEqual(
            0.05,
            self.runtime.parabolic_chord_error((0.0, -190.0, 0.0), step))

    def test_protocol_max_gravity_adapts_to_five_centimetres_error(self):
        step = self.runtime.curvature_limited_substep(
            (0.0, -500.0, 0.0))

        self.assertAlmostEqual(0.0282842712474619, step)
        self.assertAlmostEqual(
            0.05,
            self.runtime.parabolic_chord_error((0.0, -500.0, 0.0), step))

    def test_expanded_segment_bounds_are_conservative_and_reject_far_points(self):
        start = (0.0, 0.0, 0.0)
        end = (10.0, 0.0, 0.0)

        self.assertTrue(self.runtime.point_in_expanded_segment_bounds(
            (5.0, 15.0, 0.0), start, end, 15.0))
        self.assertFalse(self.runtime.point_in_expanded_segment_bounds(
            (5.0, 15.0001, 0.0), start, end, 15.0))
        # Corner points may pass this cheap conservative test; the exact
        # point-to-segment test remains authoritative immediately afterwards.
        corner = (25.0, 15.0, 0.0)
        self.assertTrue(self.runtime.point_in_expanded_segment_bounds(
            corner, start, end, 15.0))
        self.assertGreater(
            self.runtime.point_segment_distance_sq(corner, start, end),
            15.0 ** 2)


if __name__ == '__main__':
    unittest.main()
