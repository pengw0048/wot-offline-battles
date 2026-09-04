import collections
import json
import math
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
TESTS_ROOT = ROOT / 'tests'
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(CLIENT_SCRIPTS))
sys.path.insert(0, str(TESTS_ROOT))

from gui.mods.offline_lan_0922.ai.planner import BattleDirector
from server_bot_ai import BotPlanner
import test_port_0922_bot_runtime as runtime_fixtures
from effective_params_fixture import bot_default_crew_factors
from test_port_0922_fjord_departure import (
    ROLES, _EpisodeMonitor, _flat_graph,
)


NON_SPG_LINEUP = (
    ('heavyTank',) * 6 + ('mediumTank',) * 5 +
    ('AT-SPG',) * 2 + ('lightTank',) * 2)


class HimmelsdorfDepartureTests(unittest.TestCase):
    def setUp(self):
        self._gui_modules = dict(
            (key, value) for key, value in sys.modules.items()
            if key == 'gui' or key.startswith('gui.'))

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._gui_modules)

    @staticmethod
    def _route_profile(bot_id):
        """Return a non-SPG profile suitable for the v0.6.6 route roster."""
        return {
            'vehicle_name': 'departure-test:%d' % bot_id,
            'class_tag': 'mediumTank',
            'dominant_role': 'support',
            'roles': {
                'brawler': 0.5,
                'flanker': 0.5,
                'scout': 0.5,
                'sniper': 0.5,
                'support': 0.5,
                'artillery': 0.0,
            },
            'shells': (),
            'desired_range': 180.0,
            'fire_range': 500.0,
            'armor': 80.0,
            'speed': 15.0,
        }

    @staticmethod
    def _synthetic_director():
        director = BattleDirector('07_lakeville', 'connector-motion-contract')
        agent = director.register_profile(
            93, 1, HimmelsdorfDepartureTests._route_profile(93),
            'Connector contract Bot')
        agent['route'] = {
            'id': 'connector-contract',
            'waypoints': (
                (0.0, -40.0, False),
                (0.0, -65.0, False),
                (40.0, 40.0, False),
            ),
        }
        agent['route_started'] = False
        return director, agent

    def test_local_connector_filter_requires_forward_motion(self):
        """Parked and reversing hulls retain the authored spawn connector."""
        cases = (
            (0.0, (0.0, 0.0, -65.0), 1),
            (-6.0, (0.0, 0.0, -65.0), 1),
            (6.0, (40.0, 0.0, 40.0), 2),
        )
        for speed, expected_target, expected_index in cases:
            with self.subTest(speed=speed):
                director, agent = self._synthetic_director()

                order = director.order_for(
                    93, (0.0, 0.0, -20.0), 0.0, speed,
                    1000, 1000, 0.0)

                self.assertEqual(expected_target, order['move_position'])
                self.assertEqual(expected_index, order['route_index'])
                self.assertEqual((0.0, 0.0, -20.0), order['route_anchor'])
                self.assertTrue(order['route_join'])
                self.assertTrue(agent['route_started'])

    def test_report_roster_parked_route_joins_match_server_contract(self):
        """Rebuild the reported map, human slot, Bot slots and baked routes.

        The report contains one human in team-2 slot zero, leaving all fifteen
        team-1 Bots and fourteen team-2 Bots. The exact random vehicle roster is
        not present in the report, so uniform non-SPG profiles isolate the
        connector contract while retaining the real capacity allocation, spawn
        poses and route geometry.
        """
        graph = json.loads(
            (ROOT / 'navgraphs' / '86_himmelsdorf_winter.json').read_text())
        director = BattleDirector(
            '86_himmelsdorf_winter', 'reported-round-1',
            bases={1: tuple(graph['bases'][0]),
                   2: tuple(graph['bases'][1])},
            bounds=tuple(graph['bounds']), baked_routes=graph['routes'])
        server = BotPlanner()
        route_counts = collections.Counter()
        team_one_hill = []
        bot_ids = []

        for team in (1, 2):
            for slot in range(15):
                if team == 2 and slot == 0:
                    continue
                bot_id = slot + 1 if team == 1 else slot + 16
                bot_ids.append(bot_id)
                profile = self._route_profile(bot_id)
                agent = director.register_profile(
                    bot_id, team, profile, 'Bot %d' % bot_id)
                raw_spawn = graph['spawn_formations'][str(team)][slot]
                spawn = (float(raw_spawn[0]), float(raw_spawn[1]),
                         float(raw_spawn[2]))
                yaw = float(raw_spawn[3])

                local_order = director.order_for(
                    bot_id, spawn, yaw, 0.0, 1000, 1000, 0.0)
                route = dict(agent['route'])
                route['waypoints'] = [
                    {'x': float(point[0]), 'y': spawn[1],
                     'z': float(point[1])}
                    for point in agent['route']['waypoints']
                ]
                server_bot = {
                    'id': bot_id,
                    'team': team,
                    'slot': slot,
                    'profile': profile,
                    'route': route,
                    'state': {
                        'x': spawn[0], 'y': spawn[1], 'z': spawn[2],
                        'yaw': yaw, 'speed': 0.0,
                    },
                }
                (server_route_id, server_index, server_point,
                 server_anchor, server_join) = server._route(server_bot, 0.0)

                route_id = agent['route']['id']
                route_counts[(team, route_id)] += 1
                self.assertEqual(route_id, local_order['route_id'])
                self.assertEqual(server_route_id, local_order['route_id'])
                self.assertEqual(server_index, local_order['route_index'])
                self.assertEqual(
                    (server_point['x'], server_point['y'], server_point['z']),
                    local_order['move_position'])
                self.assertEqual(
                    (server_anchor['x'], server_anchor['y'],
                     server_anchor['z']),
                    spawn)
                self.assertEqual(spawn, local_order['route_anchor'])
                self.assertTrue(server_join)
                self.assertTrue(local_order['route_join'])

                if team == 1 and route_id == 'hill':
                    dx = local_order['move_position'][0] - spawn[0]
                    dz = local_order['move_position'][2] - spawn[2]
                    heading_error = abs(
                        (math.atan2(dx, dz) - yaw + math.pi) %
                        (2.0 * math.pi) - math.pi)
                    team_one_hill.append(
                        (bot_id, local_order['route_index'], heading_error))

        self.assertEqual(29, len(bot_ids))
        self.assertNotIn(16, bot_ids)
        self.assertEqual(
            [4, 5, 5],
            sorted(route_counts[(2, route_id)]
                   for route_id in ('banana', 'hill', 'rail')))
        self.assertEqual(
            [5, 5, 5],
            sorted(route_counts[(1, route_id)]
                   for route_id in ('banana', 'hill', 'rail')))
        self.assertEqual(5, len(team_one_hill))
        for unused_bot_id, route_index, heading_error in team_one_hill:
            self.assertEqual(1, route_index)
            self.assertGreater(heading_error, math.pi * 0.5)

    def test_flat_report_roster_departs_at_15_and_24_fps(self):
        """Exercise 30 seconds of the copied authority without native claims.

        This is a pure-data regression over the production Himmelsdorf route
        topology, formations, planner, driver, traffic law and copied vehicle
        physics. It cannot prove #1513 BigWorld collision or presentation on
        Windows, but it does reproduce the reported team-2 human slot zero and
        all 29 non-SPG Bot spawn/route-join decisions at two render cadences.
        """
        module = runtime_fixtures._load()
        original_factors = module.loadout.attribute_factors
        module.loadout.attribute_factors = bot_default_crew_factors
        self.addCleanup(
            setattr, module.loadout, 'attribute_factors', original_factors)
        graph = _flat_graph('86_himmelsdorf_winter')
        bots = []
        for team in (1, 2):
            for slot, class_tag in enumerate(NON_SPG_LINEUP):
                if team == 2 and slot == 0:
                    continue
                dominant_role, roles = ROLES[class_tag]
                bot_id = slot + 1 if team == 1 else slot + 16
                bots.append({
                    'id': bot_id,
                    'team': team,
                    'slot': slot,
                    'name': '%s%d' % (class_tag, bot_id),
                    'vehicle': 'fake',
                    'profile': {
                        'class_tag': class_tag,
                        'dominant_role': dominant_role,
                        'roles': dict(roles),
                        'shells': [],
                        'vehicle_name': 'fake',
                        'desired_range': 200,
                        'fire_range': 500,
                    },
                })

        def spawn(team, slot):
            point = graph['spawn_formations'][str(team)][slot]
            return ((point[0], 0.0, point[2]), point[3])

        def flat_ground(unused_x, unused_z, unused_hint=0.0):
            return 0.0

        for fps in (15, 24):
            with self.subTest(fps=fps):
                runtime_box = {}

                def baked_direction(
                        position, yaw, speed, unused_descriptor,
                        maximum_distance):
                    distance = 20.0 if abs(float(speed)) > 5.0 else 15.0
                    if maximum_distance is not None:
                        distance = min(distance, float(maximum_distance))
                    end = (
                        float(position[0]) + math.sin(float(yaw)) * distance,
                        float(position[1]),
                        float(position[2]) + math.cos(float(yaw)) * distance,
                    )
                    grid = runtime_box['runtime'].navigator.grid
                    clear = grid.segment_clear(position, end)
                    return {
                        'clear': clear, 'collision': not clear, 'slope': 0.0,
                    }

                def baked_receipt(
                        position, yaw, speed, descriptor, maximum_distance):
                    result = baked_direction(
                        position, yaw, speed, descriptor, maximum_distance)
                    if not result['clear']:
                        return False
                    distance = 15.0
                    if maximum_distance is not None:
                        distance = min(distance, float(maximum_distance))
                    return {
                        'distance': distance, 'half_width': 1.6,
                        'leading': 3.5, 'origin': tuple(position),
                        'yaw': float(yaw),
                        'direction': -1 if float(speed) < 0.0 else 1,
                    }

                runtime = module.BotRuntime(
                    1,
                    descriptor_resolver=(
                        lambda unused: runtime_fixtures._combat_descriptor()),
                    direction_probe=baked_direction,
                    world_receipt_probe=baked_receipt,
                    spawn_resolver=spawn,
                    ground_probe=flat_ground,
                    physics_ground_probe=flat_ground,
                    baked_graph=graph,
                    visibility_probe=lambda *unused: False,
                    firing_lane_probe=lambda *unused: False)
                runtime_box['runtime'] = runtime
                runtime.battle_start({
                    'map': '86_himmelsdorf_winter',
                    'round_id': fps,
                    'bot_authority_id': 1,
                    'bots': bots,
                })
                starts = dict(
                    (bot_id, (state['x'], state['z']))
                    for bot_id, state in runtime.states.items())
                maximum_departure = dict((bot_id, 0.0) for bot_id in starts)
                monitor = _EpisodeMonitor()
                monitor.started['recovery'] = {}
                monitor.maximum['recovery'] = {}
                now = [0.0]
                macro_goals = {}
                peak_group = {
                    1: {'parked': 0, 'recovery': 0},
                    2: {'parked': 0, 'recovery': 0},
                }

                original_navigation = runtime.adapter.navigation_target

                def navigation_target(
                        bot_id, position, goal, strategic, state):
                    macro_goals[int(bot_id)] = tuple(goal)
                    return original_navigation(
                        bot_id, position, goal, strategic, state)

                runtime.adapter.navigation_target = navigation_target
                original_drive = runtime.adapter.driver.drive

                def drive(*args, **kwargs):
                    order = original_drive(*args, **kwargs)
                    bot_id = int(args[0])
                    position = args[2]
                    goal = macro_goals.get(bot_id, position)
                    far_macro_goal = math.hypot(
                        float(goal[0]) - float(position[0]),
                        float(goal[2]) - float(position[2])) > 15.0
                    mode = order.get('recovery_mode')
                    monitor.record(
                        'parked', bot_id,
                        far_macro_goal and mode == 'arrived', now[0])
                    monitor.record(
                        'recovery', bot_id,
                        mode in ('blocked', 'pivot_recovery', 'reverse_turn'),
                        now[0])
                    return order

                runtime.adapter.driver.drive = drive

                for frame in range(1, fps * 30 + 1):
                    now[0] = frame / float(fps)
                    runtime.update(1.0 / float(fps), now[0])
                    group = {
                        1: {'parked': 0, 'recovery': 0},
                        2: {'parked': 0, 'recovery': 0},
                    }
                    for bot_id, state in runtime.states.items():
                        distance = math.hypot(
                            state['x'] - starts[bot_id][0],
                            state['z'] - starts[bot_id][1])
                        maximum_departure[bot_id] = max(
                            maximum_departure[bot_id], distance)
                        cached = runtime._decision_cache.get(bot_id)
                        command = cached[3] if cached is not None else {}
                        mode = command.get('recovery_mode')
                        goal = macro_goals.get(bot_id, (state['x'], 0.0,
                                                       state['z']))
                        far_macro_goal = math.hypot(
                            float(goal[0]) - float(state['x']),
                            float(goal[2]) - float(state['z'])) > 15.0
                        if far_macro_goal and mode == 'arrived':
                            group[state['team']]['parked'] += 1
                        if mode in ('blocked', 'pivot_recovery',
                                    'reverse_turn'):
                            group[state['team']]['recovery'] += 1
                    for team in (1, 2):
                        for kind in ('parked', 'recovery'):
                            peak_group[team][kind] = max(
                                peak_group[team][kind], group[team][kind])

                bot_ids = sorted(runtime.states)
                monitor.finish(bot_ids, now[0])
                final_departure = dict(
                    (bot_id, math.hypot(
                        state['x'] - starts[bot_id][0],
                        state['z'] - starts[bot_id][1]))
                    for bot_id, state in runtime.states.items())
                thresholds = dict(
                    (bot_id, max(8.0, 2.0 * state['half_length']))
                    for bot_id, state in runtime.states.items())
                never_exited = [
                    bot_id for bot_id in bot_ids
                    if maximum_departure[bot_id] < thresholds[bot_id]
                ]

                self.assertEqual(29, len(bot_ids))
                self.assertNotIn(16, bot_ids)
                self.assertEqual([], sorted(
                    (bot_id, round(maximum_departure[bot_id], 3),
                     round(thresholds[bot_id], 3))
                    for bot_id in never_exited))
                self.assertEqual([], sorted(
                    (bot_id, round(final_departure[bot_id], 3),
                     round(thresholds[bot_id], 3))
                    for bot_id in bot_ids
                    if final_departure[bot_id] < thresholds[bot_id]))
                for team in (1, 2):
                    team_ids = [bot_id for bot_id in bot_ids
                                if runtime.states[bot_id]['team'] == team]
                    team_never_exited = [bot_id for bot_id in never_exited
                                         if bot_id in team_ids]
                    self.assertLess(
                        len(team_never_exited), (len(team_ids) + 1) // 2)
                    self.assertLess(
                        peak_group[team]['parked'],
                        (len(team_ids) + 1) // 2)
                    self.assertLess(
                        peak_group[team]['recovery'],
                        (len(team_ids) + 1) // 2)
                self.assertLessEqual(
                    max(monitor.maximum['parked'].values() or [0.0]),
                    0.5)
                self.assertLessEqual(
                    max(monitor.maximum['recovery'].values() or [0.0]),
                    2.0)


if __name__ == '__main__':
    unittest.main()
