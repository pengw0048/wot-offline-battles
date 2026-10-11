"""Real driver recovery after native rotation rejects a coupled drive command."""
import contextlib
import io
import math
import unittest

import test_port_0922_bot_runtime as fixture
from test_port_0922_separation_progress import _flat_graph


class RotationRecoveryTests(unittest.TestCase):
    setUp = fixture.BotRuntimeTests.setUp
    tearDown = fixture.BotRuntimeTests.tearDown

    def run_pocket(self, rear_clear=True):
        rotations, translations, decisions = [], [], []
        initial_yaw = 2.0684673285883455
        reverse_heading = initial_yaw + math.pi

        def direction(position, yaw, speed, descriptor, distance, **kwargs):
            rear = math.cos(yaw - reverse_heading) > .98
            return {'clear': rear_clear or not rear, 'slope': 0.0,
                    'collision': not rear_clear and rear}

        def rotation(bot_id, position, old_yaw, new_yaw, *unused):
            # A real building can forbid angular corner sweep while a straight
            # rear corridor remains clear. Baking alone has no angular hit.
            clear = math.hypot(position[0], position[2]) >= .55
            rotations.append((tuple(position), old_yaw, new_yaw, clear))
            return clear

        def motion(bot_id, position, yaw, speed, *unused, **kwargs):
            translations.append((tuple(position), yaw, speed))
            return 'clear' if rear_clear else 'hard'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: fixture._combat_descriptor(),
            direction_probe=direction, rotation_resolver=rotation,
            motion_resolver=motion, ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=fixture._spawn_resolver, baked_graph=_flat_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=initial_yaw,
                     speed=0.0, grounded_once=True)
        # Isolate the final local edge; retain the actual BotAdapter, driver,
        # traffic coordinator, native gates and copied physics update loop.
        runtime.adapter.navigation_target = lambda bot, pos, target, order, row: target
        runtime._server_orders[11] = {
            'move_position': (12.0, 0.0, 8.0),
            'aim_position': (12.0, 0.0, 8.0),
            'face_position': (12.0, 0.0, 8.0),
            'combat_mode': 'route', 'fire_allowed': False,
        }
        original_drive = runtime.adapter.driver.drive
        now = [0.0]

        def drive(*args, **kwargs):
            command = original_drive(*args, **kwargs)
            recorded = dict(command, movement_intent=kwargs['movement_intent'])
            decisions.append((now[0], recorded))
            return command

        runtime.adapter.driver.drive = drive
        poses = []
        with contextlib.redirect_stdout(io.StringIO()):
            for frame in range(80):
                now[0] = frame * .1
                runtime.update(.1, 1.0 + now[0])
                poses.append((state['x'], state['z'], state['yaw'], state['speed']))
        return runtime, decisions, rotations, translations, poses

    def test_blocked_rotation_waits_then_straight_reverses_and_turns_after_leaving(self):
        runtime, decisions, rotations, translations, poses = self.run_pocket()
        straight_reverse = [(at, cmd) for at, cmd in decisions
                            if cmd['throttle'] < 0.0 and cmd['turn'] == 0.0]
        self.assertTrue(straight_reverse, decisions)
        self.assertGreaterEqual(straight_reverse[0][0], 1.8)
        self.assertLess(straight_reverse[0][0], 3.0)
        self.assertTrue(any(speed < 0.0 for unused, yaw, speed in translations))
        self.assertGreater(max(math.hypot(x, z) for x, z, yaw, speed in poses), .55)
        self.assertTrue(any(clear for pos, old, new, clear in rotations))
        self.assertTrue(any(abs(yaw - poses[0][2]) > .05
                            for x, z, yaw, speed in poses))
        self.assertTrue(all(cmd['movement_intent']
                            for at, cmd in decisions))

    def test_angular_failure_cannot_authorize_a_blocked_rear_corridor(self):
        unused, decisions, rotations, translations, poses = self.run_pocket(False)
        self.assertTrue(rotations)
        self.assertFalse(any(cmd['throttle'] < 0.0 for at, cmd in decisions))
        self.assertTrue(all(math.hypot(x, z) < 1.0e-8
                            for x, z, yaw, speed in poses))


if __name__ == '__main__':
    unittest.main()
