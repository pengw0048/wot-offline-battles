"""Route bots around a destroyed hull instead of grinding against it.

A wreck is exact new static geometry: it never moves and no amount of waiting
clears it.  The corridor below is authored once and used by both the baked
navigation graph and the terrain probe, so the planner and the local driver
agree about the walls and only the wreck is new information.
"""

import collections
import math
import sys
import unittest

import test_port_0922_bot_runtime as runtime_fixtures
from effective_params_fixture import bot_default_crew_factors


CELL = 2.0
ORIGIN = (-40.0, -40.0)
CELLS = 61
# The synthetic probe stands in for a native hull sweep, so it keeps slightly
# less clearance than the 1.7 metre hull half width the collision solver uses.
CLEARANCE = 1.2
WRECK = (0.0, 0.0, 22.0)
START = (0.0, 0.0, -14.0)
GOAL = (0.0, 0.0, 50.0)
PARKED = (20.0, 0.0, 55.0)


def _main_lane(x, z):
    return -4.0 <= x <= 4.0 and -22.0 <= z <= 75.0


def _in_corridor(x, z):
    """One authored map: a main lane and a signposted bypass around it."""
    return (
        # Main lane running north. The wreck stands in it at z=22, between
        # both connectors, and no hull can pass it inside an eight-metre lane.
        _main_lane(x, z) or
        # South connector on to the bypass.
        (-4.0 <= x <= 26.0 and -6.0 <= z <= 12.0) or
        # Bypass lane.
        (16.0 <= x <= 26.0 and -6.0 <= z <= 44.0) or
        # North connector back on to the main lane.
        (-4.0 <= x <= 26.0 and 30.0 <= z <= 44.0))


def _drivable(x, z):
    return all(_in_corridor(x + dx, z + dz)
               for dx, dz in ((0.0, 0.0), (CLEARANCE, 0.0),
                              (-CLEARANCE, 0.0), (0.0, CLEARANCE),
                              (0.0, -CLEARANCE)))


def _corridor_graph():
    graph = runtime_fixtures._graph()
    directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                  (1, 0), (-1, 1), (0, 1), (1, 1))
    open_cells = [
        _drivable(ORIGIN[0] + x * CELL, ORIGIN[1] + z * CELL)
        for z in range(CELLS) for x in range(CELLS)]
    links = []
    heights = []
    for z in range(CELLS):
        for x in range(CELLS):
            if not open_cells[z * CELLS + x]:
                links.append(0)
                heights.append(None)
                continue
            heights.append(0)
            mask = 0
            for bit, (step_x, step_z) in enumerate(directions):
                next_x, next_z = x + step_x, z + step_z
                if (0 <= next_x < CELLS and 0 <= next_z < CELLS and
                        open_cells[next_z * CELLS + next_x]):
                    mask |= 1 << bit
            links.append(mask)
    graph.update({
        'cell_size': CELL, 'origin': ORIGIN,
        'bounds': (ORIGIN[0] - 1.0, ORIGIN[1] - 1.0,
                   ORIGIN[0] + CELLS * CELL + 1.0,
                   ORIGIN[1] + CELLS * CELL + 1.0),
        'width': CELLS, 'height': CELLS,
        'heights_mm': heights, 'links': links,
        'hazards': [0] * (CELLS * CELLS),
    })
    return graph


def _direction_probe(position, yaw, speed=0.0, descriptor=None,
                     maximum_distance=None):
    distance = (12.0 if maximum_distance is None else
                min(12.0, max(1.0, float(maximum_distance))))
    sine, cosine = math.sin(float(yaw)), math.cos(float(yaw))
    travelled = 0.0
    while travelled <= distance:
        if not _drivable(float(position[0]) + sine * travelled,
                         float(position[2]) + cosine * travelled):
            return {'clear': False, 'collision': True, 'slope': 0.0}
        travelled += 0.5
    return {'clear': True, 'collision': False, 'slope': 0.0}


class WreckRouteTests(unittest.TestCase):
    def setUp(self):
        self._gui_modules = dict(
            (key, value) for key, value in sys.modules.items()
            if key == 'gui' or key.startswith('gui.'))
        self.module = runtime_fixtures._load()
        original_factors = self.module.loadout.attribute_factors
        self.module.loadout.attribute_factors = bot_default_crew_factors
        self.addCleanup(setattr, self.module.loadout, 'attribute_factors',
                        original_factors)

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._gui_modules)

    def _seal_the_bypass(self):
        """Retire the bypass so the wreck blocks the only route there is."""
        module = sys.modules[__name__]
        original = module._in_corridor
        module._in_corridor = _main_lane
        self.addCleanup(setattr, module, '_in_corridor', original)

    def _drive_to_goal(self, wreck_at, seconds=75.0):
        """Return ``(arrival_seconds, modes, furthest_east, runtime)``."""
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=(
                lambda unused: runtime_fixtures._combat_descriptor()),
            direction_probe=_direction_probe,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=lambda team, slot: (
                (START if slot == 0 else wreck_at), 0.0),
            baked_graph=_corridor_graph(),
            control_seconds=0.1,
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: False)
        runtime.battle_start({
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'bots': [{
                'id': slot + 1, 'team': 1, 'slot': slot,
                'name': 'Fixture', 'vehicle': 'fake',
                'profile': {
                    'class_tag': 'heavyTank', 'dominant_role': 'brawler',
                    'roles': {'brawler': 1.0}, 'shells': [],
                    'desired_range': 200, 'fire_range': 500,
                },
            } for slot in range(2)],
        })
        runtime._apply_orders({
            'bot_order_revision': 1, 'bot_orders': [
                {'id': 1, 'move_position': GOAL, 'face_position': GOAL,
                 'combat_mode': 'route', 'fire_allowed': False},
                {'id': 2, 'move_position': wreck_at,
                 'face_position': (wreck_at[0], 0.0, wreck_at[2] + 100.0),
                 'combat_mode': 'artillery_hold',
                 'throttle_override': 0.0, 'fire_allowed': False},
            ],
        })
        wreck = runtime.states[2]
        wreck['health'] = 0
        wreck['alive'] = False
        wreck['speed'] = 0.0

        modes = collections.Counter()
        original_drive = runtime.adapter.driver.drive

        def drive(*args, **kwargs):
            order = original_drive(*args, **kwargs)
            if args[0] == 1:
                modes[order['recovery_mode']] += 1
            return order

        runtime.adapter.driver.drive = drive
        arrived = None
        furthest_east = START[0]
        self.pushing_frames = 0
        for frame in range(1, int(seconds * 30) + 1):
            runtime.update(1.0 / 30.0, frame / 30.0)
            # The server owns terminal health; keep the wreck destroyed while
            # the authority keeps simulating the round.
            wreck['health'] = 0
            wreck['alive'] = False
            state = runtime.states[1]
            furthest_east = max(furthest_east, state['x'])
            if (int(state.get('movement_dir', 0)) > 0 and
                    abs(state['x'] - wreck['x']) < 3.4 and
                    abs(state['z'] - wreck['z']) < 7.0):
                self.pushing_frames += 1
            if (arrived is None and math.hypot(
                    GOAL[0] - state['x'], GOAL[2] - state['z']) <= 3.0):
                arrived = frame / 30.0
        return arrived, modes, furthest_east, runtime

    def test_a_clear_lane_is_still_driven_straight_through(self):
        arrived, unused_modes, unused_east, unused_runtime = (
            self._drive_to_goal(PARKED))
        self.assertIsNotNone(
            arrived, 'the control lane is no longer drivable')
        self.assertLess(arrived, 20.0)

    def test_a_wreck_in_the_lane_is_routed_around_not_pushed(self):
        arrived, modes, furthest_east, unused_runtime = self._drive_to_goal(
            WRECK)
        self.assertIsNotNone(
            arrived,
            'the bot never reached its goal past the wreck; modes=%s' %
            (dict(modes),))
        # Reaching the goal by grinding through the wreck is not a pass: the
        # bypass is the only route past it, so the hull must have driven it.
        self.assertGreater(furthest_east, 12.0)

    def test_a_sealed_lane_holds_instead_of_grinding_against_the_wreck(self):
        self._seal_the_bypass()

        arrived, unused_modes, unused_east, unused_runtime = (
            self._drive_to_goal(WRECK))

        # There is no way past, which is an honest hold. Driving into the
        # corpse for the rest of the round is not.
        self.assertIsNone(arrived)
        self.assertEqual(0, self.pushing_frames)

    def test_the_navigator_marks_the_wreck_cells(self):
        unused_arrived, unused_modes, unused_east, runtime = (
            self._drive_to_goal(WRECK, seconds=1.0))
        grid = runtime.navigator.grid
        self.assertTrue(grid._static_hull_edges)
        wreck_cell = grid.cell_for(WRECK)
        self.assertGreater(
            grid.segment_penalty(
                (0.0, 0.0, 16.0), (0.0, 0.0, 28.0), 1.0), 0.0)
        self.assertEqual(
            0.0,
            grid.segment_penalty((0.0, 0.0, -20.0), (0.0, 0.0, -8.0), 1.0))
        self.assertIn(
            wreck_cell,
            set(cell for edge in grid._static_hull_edges for cell in edge))


if __name__ == '__main__':
    unittest.main()
