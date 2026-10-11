"""A rejected angular pose must not authorize its coupled powered travel."""
import json
import contextlib
import io
import math
from pathlib import Path
import unittest
from unittest import mock

import test_port_0922_bot_runtime as fixture


class BotRotationDriveTests(unittest.TestCase):
    setUp = fixture.BotRuntimeTests.setUp
    tearDown = fixture.BotRuntimeTests.tearDown

    def runtime_with_command(self, throttle=1.0, turn=-1.0, clear=False):
        # The reported IS2 yaw/turn are retained, but native scene geometry is
        # deliberately controlled: the capture did not log its angular hit.
        command = fixture.BotRuntimeTests._stationary_command()
        command.update(throttle=throttle, turn=turn, target_yaw=0.934,
                       move_position=(12.0, 0.0, 8.0),
                       movement_intent=True, recovery_mode='drive',
                       combat_mode='route')
        rotation = mock.Mock(return_value=clear)
        motion = mock.Mock(return_value='clear')
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: fixture._combat_descriptor(),
            adapter_factory=lambda *unused: fixture._FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=fixture._spawn_resolver, baked_graph=fixture._graph(),
            motion_resolver=motion, rotation_resolver=rotation)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=2.0684673285883455,
                     speed=0.0, grounded_once=True)
        return runtime, state, rotation, motion

    def test_rejected_turn_cannot_accelerate_along_old_heading(self):
        for throttle in (1.0, -0.72):
            with self.subTest(throttle=throttle):
                runtime, state, rotation, motion = self.runtime_with_command(throttle)
                old_yaw = state['yaw']
                for frame in range(6):
                    runtime.update(0.1, 1.0 + frame * 0.1)
                self.assertEqual((0.0, 0.0), (state['x'], state['z']))
                self.assertEqual(0.0, state['speed'])
                self.assertEqual(old_yaw, state['yaw'])
                self.assertGreater(rotation.call_count, 1)
                motion.assert_not_called()
                self.assertNotIn(11, runtime._decision_cache)

    def test_a_new_straight_reverse_can_use_its_exact_clear_escape(self):
        runtime, state, rotation, motion = self.runtime_with_command(-0.72, 0.0)
        runtime.update(0.1, 1.0)
        self.assertLess(state['speed'], 0.0)
        self.assertLess(state['x'], 0.0)
        rotation.assert_not_called()
        motion.assert_called()
        self.assertLess(motion.call_args.args[3], 0.0)

    def test_clear_turn_keeps_the_coupled_drive_command(self):
        runtime, state, rotation, motion = self.runtime_with_command(clear=True)
        old_yaw = state['yaw']
        runtime.update(0.1, 1.0)
        self.assertGreater(state['speed'], 0.0)
        self.assertLess(state['yaw'], old_yaw)
        rotation.assert_called_once()
        motion.assert_called_once()

    def test_straight_reverse_cannot_ignore_a_real_rear_obstacle(self):
        runtime, state, rotation, motion = self.runtime_with_command(-0.72, 0.0)
        motion.return_value = 'hard'
        runtime.update(0.1, 1.0)
        self.assertEqual((0.0, 0.0), (state['x'], state['z']))
        rotation.assert_not_called()
        motion.assert_called()

    def test_motion_diagnostic_distinguishes_rejected_rotation_from_translation(self):
        runtime, state, rotation, motion = self.runtime_with_command()
        state['_motion_stall_log'] = ((0.0, 0.0, 0.0), -5.0)
        captured = []
        runtime._finish_motion_stall = lambda row, *unused: captured.append(
            dict(row.pop('_motion_stall_pending', None) or {}))
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.update(0.1, 1.0)
        self.assertEqual('native_world', captured[0]['rotation_block_reason'])
        self.assertTrue(captured[0]['rotation_blocked'])
        self.assertTrue(captured[0]['rotation_drive_held'])
        self.assertEqual(0.0, captured[0]['actual_turn_speed'])
        self.assertEqual('clear', captured[0]['world_status'])
        self.assertEqual(0.0, captured[0]['throttle'])

    def test_blocked_turn_brakes_existing_momentum_without_teleporting(self):
        runtime, state, rotation, motion = self.runtime_with_command()
        state['speed'] = 5.0
        runtime.update(0.1, 1.0)
        self.assertGreater(state['speed'], 0.0)
        self.assertLess(state['speed'], 5.0)
        self.assertGreater(math.hypot(state['x'], state['z']), 0.0)
        motion.assert_called_once()

    def test_airfield_report_pose_is_not_an_arena_boundary_rejection(self):
        graph_path = Path(__file__).resolve().parents[1] / 'navgraphs/31_airfield.json'
        self.runtime.baked_graph = json.loads(graph_path.read_text())
        position = (85.16134455848143, -12.601318359375, -299.5936716789246)
        state = dict(id=11, half_width=1.5292890071868896,
                     half_length=3.2842938899993896)
        self.assertTrue(self.runtime._baked_pose_progress_clear(
            state, position, 2.0684673285883455,
            position, 2.0584673285883455))


if __name__ == '__main__':
    unittest.main()
