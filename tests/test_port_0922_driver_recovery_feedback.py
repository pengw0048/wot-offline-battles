"""Real collision feedback must also retire a failed recovery direction.

The fixed analytic alley isolates the Airfield report's clear-planning-ray /
failed-final-sweep contract. It is not a reconstruction of native Airfield BSP.
"""
import contextlib
import io
import math
import sys
import types
import unittest

from test_port_0922_bot_runtime import (
    _combat_descriptor, _flat_open_graph, _load,
    _plain_attribute_factors, _spawn_resolver)
from test_port_0922_world_collision import _Vector, _miss_mat_info_1513


class RecoveryAlley:
    def __init__(self, closed_front=False):
        # A 7 m hull fits the alley but cannot pivot. The rear 0.65 m lip is
        # below both planner rays, while the actual 0.6 m hull witness hits it.
        # The front mouth and the route around either wall remain open.
        self.boxes = ((-2.5, 0.0, -4.0, -1.72, 2.0, 2.0),
                      (1.72, 0.0, -4.0, 2.5, 2.0, 2.0),
                      (-1.72, 0.0, -3.7, 1.72, 0.65, -3.501))
        if closed_front:
            self.boxes += ((-1.72, 0.0, 3.501, 1.72, 0.65, 3.7),)
        self.hard = []
        self.world = types.SimpleNamespace(
            wg_collideSegment=self.collide,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        self.math = types.SimpleNamespace(Vector3=_Vector)

    def collide(self, space, start, end, *unused):
        origin = (start.x, start.y, start.z)
        delta = (end.x-start.x, end.y-start.y, end.z-start.z)
        hits = []
        if abs(delta[1]) > 1e-9:
            fraction = -start.y / delta[1]
            if 0.0 <= fraction <= 1.0:
                hits.append((fraction, (0, 1, 0)))
        for box in self.boxes:
            near, far, normal = 0.0, 1.0, None
            for axis in range(3):
                if abs(delta[axis]) < 1e-9:
                    if not box[axis] <= origin[axis] <= box[axis+3]:
                        break
                else:
                    first = (box[axis]-origin[axis]) / delta[axis]
                    last = (box[axis+3]-origin[axis]) / delta[axis]
                    sign = -1 if delta[axis] > 0 else 1
                    if first > last:
                        first, last = last, first
                    if first > near:
                        near = first
                        normal = tuple(sign if i == axis else 0 for i in range(3))
                    far = min(far, last)
                    if near > far:
                        break
            else:
                if normal is not None and 0.0 <= near <= 1.0:
                    hits.append((near, normal))
        if not hits:
            return None
        fraction, normal = min(hits, key=lambda hit: hit[0])
        return start + (end-start).scale(fraction), _Vector(*normal), 0

    def probe(self, position, yaw, speed=0.0, descriptor=None,
              maximum_distance=None):
        # Same two witness heights and lane offsets as the native planner.
        distance = min(15.0, maximum_distance or 15.0)
        for height, reach in ((0.7, min(8.0, distance)), (1.5, distance)):
            for offset in (-1.7, 0.0, 1.7):
                start = _Vector(position[0] + math.cos(yaw)*offset,
                                position[1] + height,
                                position[2] - math.sin(yaw)*offset)
                end = start + _Vector(math.sin(yaw)*reach, 0.0,
                                      math.cos(yaw)*reach)
                if self.collide(1, start, end):
                    return {'clear': False, 'collision': True, 'slope': 0.0}
        return {'clear': True, 'collision': False, 'slope': 0.0}


class DriverRecoveryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = {key: value for key, value in sys.modules.items()
                              if key == 'gui' or key.startswith('gui.')}
        self.module = _load()
        self.factors = self.module.loadout.attribute_factors
        self.module.loadout.attribute_factors = _plain_attribute_factors

    def tearDown(self):
        self.module.loadout.attribute_factors = self.factors
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self.saved_modules)

    def _runtime(self, driver_class=None, closed_front=False):
        from gui.mods.offline_lan_0922 import world_collision
        scene = RecoveryAlley(closed_front)
        descriptor = _combat_descriptor()

        def motion(bot, position, yaw, speed, desc, dt, now,
                   commit_enabled=True, motion_yaw=None):
            trace = {}
            status = world_collision.check_horizontal_collision(
                scene.world, scene.math, 1, _Vector(*position), yaw, speed,
                desc, False, dt, True, False, commit_enabled=commit_enabled,
                motion_yaw=motion_yaw, trace=trace)
            if status == 'hard':
                scene.hard.append((now, position, yaw, speed, trace))
                runtime.states[bot]['_world_contact_trace'] = trace
            return status

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            direction_probe=scene.probe, motion_resolver=motion,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_flat_open_graph())
        runtime.battle_start({
            'round_id': 5, 'map': '01_karelia', 'bot_authority_id': 1,
            'bot_skill_mode': 'brutal',
            'bots': [{'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot'}]})
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=0.0,
                     grounded_once=True)
        target = (0.0, 0.0, -15.0)
        runtime._server_orders = {11: {
            'combat_mode': 'route', 'move_position': target,
            'fire_allowed': False}}
        runtime.adapter.navigation_target = lambda *unused: target
        if driver_class is not None:
            runtime.adapter.driver = driver_class()

        def pose_clear(position, yaw, half_length, half_width):
            for x0, unused_y0, z0, x1, unused_y1, z1 in scene.boxes:
                if runtime.adapter.driver._obb_overlap(
                        position, yaw, half_length, half_width,
                        ((x0+x1)*0.5, 0.0, (z0+z1)*0.5),
                        0.0, (z1-z0)*0.5, (x1-x0)*0.5):
                    return False
            return True

        runtime.navigator.grid.hull_pose_clear = pose_clear
        runtime.rotation_resolver = (
            lambda bot, position, old, new, desc, dt, now, rate, *unused:
            pose_clear(position, new, 3.5, 1.7))
        decide = runtime.adapter.decide_with_order
        commands = []

        def observed_decide(state, order, clear):
            command = decide(state, order, clear)
            commands.append(dict(command))
            return command

        runtime.adapter.decide_with_order = observed_decide
        # Replay a recovery already in progress, as in the Tiger/Lorraine
        # captures. No failed direction or collision verdict is preinstalled.
        runtime.adapter.driver._state(11, 0, (0.0, 0.0, 0.0))[
            'recovery_time'] = 0.85
        return runtime, scene, commands, target

    def test_final_world_failure_changes_next_recovery_and_leaves_fixed_alley(self):
        for fps in (5, 10):
            with self.subTest(fps=fps), contextlib.redirect_stdout(io.StringIO()):
                runtime, scene, commands, target = self._runtime()
                dt = 1.0 / fps
                self.assertTrue(scene.probe(
                    (0.0, 0.0, 0.0), math.pi, maximum_distance=5.6)['clear'])
                runtime.update(dt, 1.0)
                self.assertEqual('reverse_turn', commands[-1]['recovery_mode'])
                self.assertTrue(scene.hard)
                self.assertEqual('solid_lane', scene.hard[0][4]['reason'])
                self.assertLess(scene.hard[0][3], 0.0)
                self.assertEqual(0.0, runtime.states[11]['z'])
                self.assertNotIn(11, runtime._decision_cache)
                self.assertNotIn(11, runtime._motion_probe_cache)
                runtime.update(dt, 1.0+dt)
                self.assertEqual('forward_escape', commands[-1]['recovery_mode'])
                self.assertGreater(runtime.states[11]['z'], 0.0)
                departed = False
                for frame in range(2, 45*fps):
                    runtime.update(dt, 1.0+frame*dt)
                    state = runtime.states[11]
                    departed |= state['z'] > 2.0+3.5
                    if math.hypot(state['x']-target[0], state['z']-target[2]) <= 1.5:
                        break
                self.assertTrue(departed)
                self.assertLessEqual(math.hypot(
                    state['x']-target[0], state['z']-target[2]), 1.5)
                # The lip never changes or vanishes; only the initial reverse
                # sweep touches it, then the hull takes the existing exit.
                self.assertEqual(1, len(scene.hard))

    def test_both_real_failed_exits_hold_instead_of_repeating_forward_escape(self):
        runtime, scene, commands, unused = self._runtime(closed_front=True)
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.update(0.1, 1.0)
            runtime.update(0.1, 1.1)
            self.assertEqual('forward_escape', commands[-1]['recovery_mode'])
            self.assertEqual(2, len(scene.hard))
            self.assertLess(scene.hard[0][3], 0.0)
            self.assertGreater(scene.hard[1][3], 0.0)
            runtime.update(0.1, 1.2)
        self.assertEqual('blocked', commands[-1]['recovery_mode'])
        self.assertTrue(commands[-1]['brake'])
        self.assertEqual(0.0, commands[-1]['throttle'])
        self.assertEqual(2, len(scene.hard))
        self.assertEqual(0.0, runtime.states[11]['z'])

    def test_expired_real_failure_allows_a_new_recovery_probe(self):
        runtime, scene, commands, target = self._runtime()
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.update(0.1, 1.0)
            # A stationary commanded hold advances the same driver clock;
            # no new motion renews the old five-second collision receipt.
            runtime.adapter.navigation_target = lambda *unused: (0.0, 0.0, 0.0)
            for frame in range(1, 62):
                runtime.update(0.1, 1.0+frame*0.1)
            runtime.adapter.navigation_target = lambda *unused: target
            runtime.adapter.driver.states[11]['recovery_time'] = 0.85
            runtime._decision_cache.clear()
            runtime.update(0.1, 7.2)
        self.assertEqual('reverse_turn', commands[-1]['recovery_mode'])
        self.assertEqual(2, len(scene.hard))

    def test_curved_recovery_keeps_only_the_unfailed_straight_exit(self):
        from gui.mods.offline_lan_0922.ai.driver import (
            LocalDriver, RECOVERY_YAW_OFFSET)
        driver = LocalDriver()
        state = driver._state(11, 0, (0.0, 0.0, 0.0))
        state.update(recovery_time=0.85, recovery_side=1.0)
        driver.remember_failure(11, math.pi+RECOVERY_YAW_OFFSET*0.5, 0.25)
        args = dict(bot_id=11, team_slot=0, position=(0.0, 0.0, 0.0),
                    yaw=0.0, speed=0.0, dt=0.1, target=(0.0, 0.0, 20.0),
                    neighbours=(), direction_clear=lambda *unused: True,
                    pose_clear=lambda unused: True)
        command = driver.drive(**args)
        self.assertEqual('reverse_turn', command['recovery_mode'])
        self.assertLess(command['throttle'], 0.0)
        self.assertEqual(0.0, command['turn'])
        args['dt'] = 0.3
        command = driver.drive(**args)
        self.assertLess(command['throttle'], 0.0)
        self.assertEqual(-1.0, command['turn'])


if __name__ == '__main__':
    unittest.main()
