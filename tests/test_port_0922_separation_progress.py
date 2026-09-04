"""Exercise close traffic with the real BotRuntime integration/contact loop.

Flat ground and synthetic descriptors isolate driver progress from native
geometry. Fixed local targets intentionally leave the server and A* out: a
local driver must finish this clear final approach beside a parked hull.
"""

import collections
import math
import sys
import unittest

import test_port_0922_bot_runtime as runtime_fixtures
from effective_params_fixture import bot_default_crew_factors


def _flat_graph():
    graph = runtime_fixtures._graph()
    width = 31
    directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                  (1, 0), (-1, 1), (0, 1), (1, 1))
    links = []
    for z in range(width):
        for x in range(width):
            links.append(sum(
                1 << index for index, (dx, dz) in enumerate(directions)
                if 0 <= x + dx < width and 0 <= z + dz < width))
    graph.update({
        'origin': (-60.0, -60.0), 'bounds': (-62.0, -62.0, 62.0, 62.0),
        'width': width, 'height': width,
        'heights_mm': [0] * (width * width),
        'links': links, 'hazards': [0] * (width * width),
    })
    return graph


class SeparationProgressTests(unittest.TestCase):
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

    def test_close_target_beside_parked_hull_does_not_become_an_orbit(self):
        for neighbour, goal in (
                ((3.0, 0.0, 0.0), (0.0, 0.0, 8.0)),
                ((-3.0, 0.0, 0.0), (0.0, 0.0, 8.0)),
                ((0.0, 0.0, 6.0), (6.0, 0.0, 8.0)),
                ((0.0, 0.0, 6.0), (-6.0, 0.0, 8.0))):
            with self.subTest(neighbour=neighbour, goal=goal):
                runtime = self.module.BotRuntime(
                    1, descriptor_resolver=(
                        lambda unused: runtime_fixtures._combat_descriptor()),
                    direction_probe=lambda *unused: {
                        'clear': True, 'collision': False, 'slope': 0.0},
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=lambda team, slot: (
                        ((0.0, 0.0, 0.0) if slot == 0 else neighbour), 0.0),
                    baked_graph=_flat_graph(),
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
                runtime.adapter.navigation_target = (
                    lambda bot_id, position, target, strategic, state: target)
                runtime._apply_orders({
                    'bot_order_revision': 1, 'bot_orders': [
                        {'id': 1, 'move_position': goal, 'face_position': goal,
                         'combat_mode': 'route', 'fire_allowed': False},
                        {'id': 2, 'move_position': neighbour,
                         'face_position': (neighbour[0], 0.0,
                                           neighbour[2] + 100.0),
                         'combat_mode': 'artillery_hold',
                         'throttle_override': 0.0, 'fire_allowed': False},
                    ],
                })
                modes = collections.Counter()
                original_drive = runtime.adapter.driver.drive

                def drive(*args, **kwargs):
                    order = original_drive(*args, **kwargs)
                    if args[0] == 1:
                        modes[order['recovery_mode']] += 1
                    return order

                runtime.adapter.driver.drive = drive
                for frame in range(1, 1801):
                    runtime.update(1.0 / 30.0, frame / 30.0)

                state = runtime.states[1]
                # A nearby teammate must not replace the clear route with
                # repulsion steering. Physical contact can still separate the
                # hulls while the Bot finishes its original approach.
                self.assertEqual(0, modes['avoid'])
                self.assertLessEqual(
                    math.hypot(goal[0] - state['x'], goal[2] - state['z']),
                    1.5, 'the local driver kept orbiting its clear target')
                self.assertLess(abs(state['speed']), 0.1)


if __name__ == '__main__':
    unittest.main()
