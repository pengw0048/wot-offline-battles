"""Two friendly hulls meeting inside Great Wall's gatehouse must both leave.

The map's only east crossing is a gatehouse whose baked corridor is one cell
wide at its north mouth and two cells inside, and both teams' ``wall_pass``
and ``valley`` routes cross it in opposite directions. Every head-on swing
offset is blocked in there, so an unbounded crossing hold stopped both hulls
and each driver then timed out into a steered reverse that parked its hull
across the passage.
"""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

import test_port_0922_bot_runtime as runtime_fixtures
from effective_params_fixture import bot_default_crew_factors
from test_port_0922_fjord_departure import _bots


ROOT = Path(__file__).resolve().parents[1]
MAP_NAME = '59_asia_great_wall'
# The gatehouse passage, from the bake's own grid-phase record.
PASSAGE_X = 404.0
SOUTH_POSE = (PASSAGE_X, -158.0, 0.0)
NORTH_POSE = (PASSAGE_X, -142.0, math.pi)
# The gatehouse interior, from the baked passage rows.
PASSAGE_SOUTH = -158.0
PASSAGE_NORTH = -140.0
SECONDS = 30.0
FPS = 15


def _flat_graph():
    path = ROOT / 'navgraphs' / ('%s.json' % MAP_NAME)
    graph = copy.deepcopy(json.loads(path.read_text()))
    # Keep the production topology, hazards, routes and formations and isolate
    # traffic from four-metre height sampling, exactly as the departure
    # fixtures do.
    graph['heights_mm'] = [
        0 if value is not None else None for value in graph['heights_mm']]
    return graph


class GreatWallGateTests(unittest.TestCase):
    def setUp(self):
        self._gui_modules = dict(
            (key, value) for key, value in sys.modules.items()
            if key == 'gui' or key.startswith('gui.'))

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._gui_modules)

    def test_opposed_hulls_in_the_gatehouse_both_leave_the_passage(self):
        module = runtime_fixtures._load()
        original_factors = module.loadout.attribute_factors
        module.loadout.attribute_factors = bot_default_crew_factors
        self.addCleanup(
            setattr, module.loadout, 'attribute_factors', original_factors)
        graph = _flat_graph()
        runtime_box = {}

        def spawn(team, slot):
            if team == 2 and slot in (0, 1):
                x, z, yaw = SOUTH_POSE if slot == 0 else NORTH_POSE
                return ((x, 0.0, z), yaw)
            point = graph['spawn_formations'][str(team)][slot]
            return ((point[0], 0.0, point[2]), point[3])

        def ground(unused_x, unused_z, unused_hint=0.0):
            return 0.0

        def baked_direction(position, yaw, speed, unused_descriptor,
                            maximum_distance):
            grid = runtime_box['runtime'].navigator.grid
            distance = 20.0 if abs(float(speed)) > 5.0 else 15.0
            if maximum_distance is not None:
                distance = min(distance, max(0.5, float(maximum_distance)))
            end = (float(position[0]) + math.sin(float(yaw)) * distance,
                   float(position[1]),
                   float(position[2]) + math.cos(float(yaw)) * distance)
            clear = grid.segment_clear(position, end)
            return {'clear': clear, 'collision': not clear,
                    'water': False, 'slope': 0.0}

        def baked_receipt(position, yaw, speed, descriptor, maximum_distance):
            if not baked_direction(position, yaw, speed, descriptor,
                                   maximum_distance)['clear']:
                return False
            distance = 15.0 if maximum_distance is None else min(
                15.0, max(0.5, float(maximum_distance)))
            return {'distance': distance, 'half_width': 1.6, 'leading': 3.5,
                    'origin': tuple(position), 'yaw': float(yaw),
                    'direction': -1 if float(speed) < 0.0 else 1}

        runtime = module.BotRuntime(
            1,
            descriptor_resolver=lambda unused: (
                runtime_fixtures._combat_descriptor()),
            direction_probe=baked_direction,
            world_receipt_probe=baked_receipt,
            spawn_resolver=spawn,
            ground_probe=ground, physics_ground_probe=ground,
            baked_graph=graph,
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: False)
        runtime_box['runtime'] = runtime
        runtime.battle_start({'map': MAP_NAME, 'round_id': 7,
                              'bot_authority_id': 1, 'bots': _bots()})
        opposed = [bot_id for bot_id, state in runtime.states.items()
                   if int(state.get('team', 0)) == 2 and
                   abs(state['x'] - PASSAGE_X) < 1.0 and
                   -160.0 < state['z'] < -140.0]
        self.assertEqual(2, len(opposed))

        held = dict((bot_id, 0.0) for bot_id in opposed)
        broadside = dict((bot_id, 0.0) for bot_id in opposed)
        original_adjust = runtime._traffic_coordinator.adjust
        clock = [0.0]

        def adjust(bot_id, source, command, neighbours, now, direction_clear):
            result = original_adjust(bot_id, source, command, neighbours, now,
                                     direction_clear)
            if (int(bot_id) in held and
                    result.get('traffic_mode') == 'head_on_blocked'):
                held[int(bot_id)] += 1.0 / FPS
            return result

        runtime._traffic_coordinator.adjust = adjust

        for frame in range(1, int(FPS * SECONDS) + 1):
            clock[0] = frame / float(FPS)
            runtime.update(1.0 / float(FPS), clock[0])
            for bot_id in opposed:
                state = runtime.states[bot_id]
                if (-160.0 < state['z'] < -140.0 and
                        abs(math.sin(state['yaw'])) > 0.5):
                    broadside[bot_id] += 1.0 / FPS

        # Both hulls have to leave the passage: the northbound one through the
        # wall, the one facing the other way back out of its south mouth.
        # The passage has to be clear again: one hull through the wall, the
        # other back out of the mouth it came from.
        self.assertEqual([], [
            (bot_id, round(runtime.states[bot_id]['z'], 1))
            for bot_id in opposed
            if PASSAGE_SOUTH <= runtime.states[bot_id]['z'] <= PASSAGE_NORTH])
        # Neither may spend the round stopped by the crossing hold, and
        # neither may sit broadside in a passage no hull can turn in.
        self.assertLessEqual(max(held.values()), 5.0)
        self.assertLessEqual(max(broadside.values()), 5.0)


if __name__ == '__main__':
    unittest.main()
