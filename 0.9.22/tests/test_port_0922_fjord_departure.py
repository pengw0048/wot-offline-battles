import copy
import json
import math
from pathlib import Path
import sys
import unittest

import test_port_0922_bot_runtime as runtime_fixtures
from effective_params_fixture import bot_default_crew_factors


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'


ROLES = {
    'heavyTank': ('brawler', {
        'brawler': .95, 'support': .45, 'flanker': .1,
        'sniper': .1, 'scout': 0.0, 'artillery': 0.0,
    }),
    'mediumTank': ('support', {
        'brawler': .4, 'support': .8, 'flanker': .7,
        'sniper': .45, 'scout': .35, 'artillery': 0.0,
    }),
    'AT-SPG': ('sniper', {
        'brawler': .2, 'support': .7, 'flanker': .1,
        'sniper': .95, 'scout': 0.0, 'artillery': 0.0,
    }),
    'lightTank': ('scout', {
        'brawler': .1, 'support': .4, 'flanker': .85,
        'sniper': .3, 'scout': 1.0, 'artillery': 0.0,
    }),
    'SPG': ('artillery', {
        'brawler': 0.0, 'support': .2, 'flanker': 0.0,
        'sniper': .2, 'scout': 0.0, 'artillery': 1.0,
    }),
}
LINEUP = (
    ('heavyTank',) * 6 + ('mediumTank',) * 4 +
    ('AT-SPG',) * 2 + ('lightTank',) + ('SPG',) * 2)


def _flat_graph(map_name):
    path = PORT_ROOT / 'navgraphs' / ('%s.json' % map_name)
    graph = copy.deepcopy(json.loads(path.read_text()))
    # Keep the production x/z topology, hazards, routes and formations. The
    # baked graph samples terrain on a four-metre grid, so feeding its nearest
    # height directly to copied per-frame physics creates false 0.6m ledges.
    # Flatten only height for this traffic/navigation isolation fixture.
    graph['heights_mm'] = [
        0 if value is not None else None
        for value in graph['heights_mm']
    ]
    return graph


def _bots():
    result = []
    for team in (1, 2):
        for slot, class_tag in enumerate(LINEUP):
            if team == 1 and slot == 0:
                continue
            dominant_role, roles = ROLES[class_tag]
            bot_id = slot + 1 if team == 1 else slot + 16
            result.append({
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
    return result


class _EpisodeMonitor(object):
    def __init__(self):
        self.started = {'traffic': {}, 'parked': {}}
        self.maximum = {'traffic': {}, 'parked': {}}

    def record(self, kind, bot_id, active, now):
        started = self.started[kind]
        maximum = self.maximum[kind]
        if active:
            started.setdefault(bot_id, float(now))
            return
        since = started.pop(bot_id, None)
        if since is not None:
            maximum[bot_id] = max(
                maximum.get(bot_id, 0.0), float(now) - since)

    def finish(self, bot_ids, now):
        for kind in self.started:
            for bot_id in bot_ids:
                self.record(kind, bot_id, False, now)


class SpawnDepartureTests(unittest.TestCase):
    def setUp(self):
        self._gui_modules = dict(
            (key, value) for key, value in sys.modules.items()
            if key == 'gui' or key.startswith('gui.'))

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._gui_modules)

    def test_flat_spawn_traffic_departs_at_15_and_24_fps(self):
        module = runtime_fixtures._load()
        original_factors = module.loadout.attribute_factors
        module.loadout.attribute_factors = bot_default_crew_factors
        self.addCleanup(
            setattr, module.loadout, 'attribute_factors', original_factors)
        bots = _bots()

        def spawn(team, slot):
            point = graph['spawn_formations'][str(team)][slot]
            return ((point[0], 0.0, point[2]), point[3])

        def flat_ground(unused_x, unused_z, unused_hint=0.0):
            return 0.0

        for map_name, fps in (
                ('33_fjord', 15), ('33_fjord', 24),
                ('31_airfield', 15), ('31_airfield', 24)):
            graph = _flat_graph(map_name)
            with self.subTest(map_name=map_name, fps=fps):
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
                    'map': map_name,
                    'round_id': fps,
                    'bot_authority_id': 1,
                    'bots': bots,
                })
                starts = dict(
                    (bot_id, (state['x'], state['z']))
                    for bot_id, state in runtime.states.items())
                monitor = _EpisodeMonitor()
                now = [0.0]
                macro_goals = {}

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
                    position = args[1]
                    goal = macro_goals.get(bot_id, position)
                    far_macro_goal = math.hypot(
                        float(goal[0]) - float(position[0]),
                        float(goal[2]) - float(position[2])) > 15.0
                    monitor.record(
                        'parked', bot_id,
                        far_macro_goal and
                        order.get('recovery_mode') == 'arrived',
                        now[0])
                    return order

                runtime.adapter.driver.drive = drive
                original_traffic = runtime._traffic_throttle

                def traffic_throttle(
                        source, command, neighbours, physics_params=None):
                    throttle, waiting = original_traffic(
                        source, command, neighbours, physics_params)
                    monitor.record(
                        'traffic', int(source['id']), waiting, now[0])
                    return throttle, waiting

                runtime._traffic_throttle = traffic_throttle

                for frame in range(1, fps * 30 + 1):
                    now[0] = frame / float(fps)
                    runtime.update(1.0 / float(fps), now[0])

                non_spg_ids = [
                    bot_id for bot_id, state in runtime.states.items()
                    if state['profile']['class_tag'] != 'SPG'
                ]
                monitor.finish(non_spg_ids, now[0])
                distances = dict(
                    (bot_id, math.hypot(
                        state['x'] - starts[bot_id][0],
                        state['z'] - starts[bot_id][1]))
                    for bot_id, state in runtime.states.items()
                    if bot_id in non_spg_ids)

                self.assertEqual(29, len(runtime.states))
                self.assertEqual(25, len(non_spg_ids))
                self.assertEqual([], sorted(
                    (bot_id, round(distance, 3))
                    for bot_id, distance in distances.items()
                    if distance <= 20.0))
                self.assertLessEqual(
                    max(monitor.maximum['traffic'].values() or [0.0]),
                    5.0)
                self.assertLessEqual(
                    max(monitor.maximum['parked'].values() or [0.0]),
                    0.5)


if __name__ == '__main__':
    unittest.main()
