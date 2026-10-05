"""Rotation geometry retains live sequential poses without traffic fields."""
import copy
import struct
import unittest

import test_port_0922_bot_runtime as fixture


def freeze(value):
    if isinstance(value, float):
        return ('float64', struct.pack('!d', value))
    if isinstance(value, dict):
        return ('dict', tuple((freeze(key), freeze(item)) for key, item in
                              sorted(value.items(), key=lambda row: repr(row[0]))))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(freeze(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, tuple(sorted(
            (freeze(item) for item in value), key=repr)))
    if hasattr(value, '__dict__'):
        return (type(value).__name__, freeze(vars(value)))
    return value


def powered_runtime(case, baseline=False):
    command = fixture.BotRuntimeTests._stationary_command()
    command.update(throttle=1.0, turn=0.7, target_yaw=1.0,
                   move_position=(0.0, 0.0, 45.0), movement_intent=True,
                   recovery_mode='drive', combat_mode='route')
    runtime = case.module.BotRuntime(
        1, descriptor_resolver=lambda unused: fixture._combat_descriptor(),
        adapter_factory=lambda *unused: fixture._FixedAdapter(command),
        direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
        ground_probe=lambda *unused: 0.0,
        physics_ground_probe=lambda *unused: 0.0,
        spawn_resolver=fixture._spawn_resolver,
        baked_graph=fixture._flat_open_graph(),
        control_seconds=case.module.WORKER_CONTROL_SECONDS)
    runtime.battle_start(dict(case.start, bots=[
        {'id': 11 + index, 'team': 1 if index < 15 else 2,
         'slot': index if index < 15 else index - 15, 'name': 'Bot'}
        for index in range(29)]))
    runtime.debug_logging = False
    for index, state in enumerate(runtime.states.values()):
        state.update(x=-25.0 + (index % 6) * 7.5,
                     y=0.0, z=-20.0 + (index // 6) * 7.5,
                     yaw=0.03 * (index % 3), speed=1.0,
                     grounded_once=True)
    if baseline:
        runtime._rotation_neighbours_for = runtime._neighbours_for
    return runtime


class RotationNeighboursTests(unittest.TestCase):
    setUp = fixture.BotRuntimeTests.setUp
    tearDown = fixture.BotRuntimeTests.tearDown

    def test_projection_preserves_supplied_order_dead_geometry_and_defaults(self):
        runtime = self.module.BotRuntime(1)
        runtime.states = {
            11: dict(id=11, x=0.0),
            12: dict(id=12, x=4.0, y=2.0, z=3.0, yaw=.4,
                     alive=False, speed=9.0, push_x=5.0,
                     collision_shape=(1.0, 2.0, -.5, 1.5)),
            13: dict(id=13),
        }
        supplied = [dict(id=11, position=(7.0, 0.0, 8.0), dims=(1, 2, 3))]
        before = copy.deepcopy((runtime.states, supplied))
        actual = runtime._rotation_neighbours_for(runtime.states[11], supplied)
        original = runtime._neighbours_for(runtime.states[11], supplied)
        self.assertIs(supplied[0], actual[0])
        self.assertEqual(len(original), len(actual))
        for expected, row in zip(original, actual):
            self.assertEqual(expected.get('position'), row.get('position'))
            self.assertEqual(expected.get('yaw', 0.0), row.get('yaw', 0.0))
            self.assertEqual(self.module.tank_collision._tank_shape(expected),
                             self.module.tank_collision._tank_shape(row))
        self.assertEqual({'position', 'shape', 'yaw'}, set(actual[1]))
        self.assertEqual(before, (runtime.states, supplied))

    def test_rotation_results_match_for_contacts_vertical_levels_and_wrecks(self):
        runtime = self.module.BotRuntime(1)
        kernel = self.module.tank_collision
        source = dict(id=11, x=0.0, y=0.0, z=0.0)
        runtime.states = {11: source, 12: dict(
            id=12, x=3.1, y=0.0, z=2.0, yaw=.2, alive=False,
            collision_shape=(1.5, 3.5, -.8, 2.0))}
        for height in (0.0, 5.0):
            runtime.states[12]['y'] = height
            for x in (2.0, 3.1, 5.0, 30.0):
                runtime.states[12]['x'] = x
                for angle in (-.4, .2, 1.1):
                    inputs = ((0.0, 0.0, 0.0), 0.0, angle,
                              kernel.DEFAULT_SHAPE)
                    old = kernel.rotation_fraction(
                        *inputs, runtime._neighbours_for(source, []))
                    new = kernel.rotation_fraction(
                        *inputs, runtime._rotation_neighbours_for(source, []))
                    self.assertEqual(old, new)

    def test_each_projection_reads_prior_actor_mutations(self):
        runtime = self.module.BotRuntime(1)
        runtime.states = dict((index, dict(id=index, x=float(index), y=0.0,
                                         z=0.0, yaw=0.0))
                              for index in range(29))
        for actor, source in runtime.states.items():
            rows = runtime._rotation_neighbours_for(source, [])
            expected = [self.module._position(raw)
                        for key, raw in runtime.states.items() if key != actor]
            self.assertEqual(expected, [row['position'] for row in rows])
            source.update(x=source['x'] + 100.0, yaw=.7)

    def test_full_powered_update_matches_and_reads_live_sequential_poses(self):
        baseline = powered_runtime(self, baseline=True)
        candidate = powered_runtime(self)
        kernel = self.module.tank_collision
        rotation = kernel.rotation_fraction
        trace = []
        active = [None]
        def observed(position, yaw, candidate_yaw, shape, others, **kwargs):
            runtime = active[0]
            expected = [self.module._position(raw)
                        for raw in runtime.states.values()
                        if self.module._position(raw) != position]
            # No supplied hulls; all 28 peers retain the current actor order.
            self.assertEqual(expected, [row['position'] for row in others])
            trace.append((position, yaw, candidate_yaw,
                          tuple((row['position'], row.get('yaw', 0.0),
                                 kernel._tank_shape(row)) for row in others)))
            return rotation(position, yaw, candidate_yaw, shape, others, **kwargs)
        kernel.rotation_fraction = observed
        try:
            for frame in range(3):
                results = []
                snapshots = []
                traces = []
                for runtime in (baseline, candidate):
                    active[0] = runtime
                    trace[:] = []
                    results.append(runtime.update(.1, 1.0 + frame * .1))
                    snapshots.append(freeze(runtime.states))
                    traces.append(list(trace))
                self.assertEqual(results[0], results[1])
                self.assertEqual(snapshots[0], snapshots[1])
                self.assertEqual(traces[0], traces[1])
                self.assertEqual(29, len(traces[0]))
                if frame == 0:
                    # The second actor already sees the first actor's new pose.
                    self.assertNotEqual(traces[0][0][0], traces[0][1][3][0][0])
                    self.assertNotEqual(traces[0][0][1], traces[0][1][3][0][1])
        finally:
            kernel.rotation_fraction = rotation


if __name__ == '__main__':
    unittest.main()
