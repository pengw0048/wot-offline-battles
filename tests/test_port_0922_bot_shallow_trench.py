"""Legacy Bot support across gradual terrain depressions at real cadences."""
import math
import unittest

import test_port_0922_bot_runtime as fixture


class BotShallowTrenchTests(unittest.TestCase):
    def setUp(self):
        self.module = fixture._load()
        self.calls = []

    def runtime(self, terrain):
        def probe(x, z, hint):
            self.calls.append((x, z, hint))
            return terrain(x, z)
        runtime = self.module.BotRuntime(1, physics_ground_probe=probe)
        runtime._turn_speeds[11] = 0.0
        return runtime

    def state(self, **changes):
        state = dict(id=11, x=0.0, y=0.0, z=0.0, yaw=0.0, speed=1.0,
                     half_length=3.5, half_width=1.5, vertical_speed=0.0,
                     airborne=False, grounded_once=True, last_drive_pitch=0.0,
                     terrain_pitch=0.0, pitch=0.0, roll=0.0)
        state.update(changes)
        return state

    def test_gradual_trench_cannot_swallow_supported_tracks(self):
        # The captured Stalingrad T-34-85 was almost 1 m below its neighbours.
        # This analytic V-shaped trench recreates the missing support law,
        # not the map mesh. Both opposing track ends remain on the banks.
        for fps in (15, 24, 60):
            for speed in (0.2, 1.0):
                for axis in ('x', 'z'):
                    with self.subTest(fps=fps, speed=speed, axis=axis):
                        runtime = self.runtime(lambda x, z: -max(
                            0.0, 1.0 - abs(x if axis == 'x' else z) / 0.7))
                        state = self.state(speed=speed)
                        state[axis] = -0.7
                        step = 1.0 / fps
                        samples = int(math.ceil(1.4 / (speed * step)))
                        minimum = 0.0
                        for index in range(samples + 1):
                            before = (state['x'], state['y'], state['z'])
                            state[axis] = min(0.7, -0.7 + index * speed * step)
                            self.calls[:] = []
                            self.assertFalse(runtime._integrate_vertical_motion(
                                state, step, tick_pose=before))
                            minimum = min(minimum, state['y'])
                            self.assertLessEqual(len(self.calls), 5)
                        # The check accumulates tiny drops, with at most its
                        # 1 cm contact tolerance before the banks take over.
                        self.assertGreaterEqual(minimum, -0.011)
                        self.assertAlmostEqual(0.0, state['y'], places=6)

    def test_opposing_support_interpolates_an_ordinary_downhill_plane(self):
        terrain = lambda x, z: -0.03 * x - 0.02 * z
        runtime = self.runtime(terrain)
        state = self.state(terrain_pitch=math.atan(0.02))
        for unused in range(60):
            before = (state['x'], state['y'], state['z'])
            state['x'] += 0.04
            state['z'] += 0.1
            self.assertFalse(runtime._integrate_vertical_motion(
                state, 0.1, tick_pose=before))
            self.assertAlmostEqual(terrain(state['x'], state['z']),
                                   state['y'], places=6)
        self.assertLess(state['y'], -0.19)

    def test_side_wall_top_cannot_become_a_trench_bridge(self):
        runtime = self.runtime(lambda x, z: 2.0 if x > 1.0 else -1.0)
        state = self.state()
        self.assertFalse(runtime._integrate_vertical_motion(state, 0.1))
        self.assertLess(state['y'], 0.0)
        self.assertTrue(state['airborne'])

    def test_one_remaining_bank_does_not_prevent_a_real_fall(self):
        runtime = self.runtime(lambda x, z: 0.0 if z < -3.0 else -2.0)
        state = self.state()
        self.assertFalse(runtime._integrate_vertical_motion(state, 0.1))
        self.assertLess(state['y'], 0.0)
        self.assertTrue(state['airborne'])

    def test_already_embedded_body_is_not_teleported_onto_high_banks(self):
        runtime = self.runtime(lambda x, z: 0.0 if abs(x) > 1.0 else -1.0)
        state = self.state(y=-0.3)
        self.assertFalse(runtime._integrate_vertical_motion(state, 0.1))
        self.assertLessEqual(state['y'], -0.3)
        self.assertTrue(state['airborne'])

    def test_flat_ground_keeps_one_native_support_column(self):
        runtime = self.runtime(lambda x, z: 0.0)
        state = self.state()
        for unused in range(20):
            self.calls[:] = []
            self.assertFalse(runtime._integrate_vertical_motion(state, 0.04))
            self.assertEqual(1, len(self.calls))
            self.assertEqual(0.0, state['y'])


if __name__ == '__main__':
    unittest.main()
