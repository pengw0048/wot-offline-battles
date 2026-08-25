import copy
import json
import math
import sys
from pathlib import Path
import os
import unittest


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai import cover, maps, reviewed_routes_20260811
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import LocalDriver
from gui.mods.offline_lan_0922.ai.planner import (
    BattleDirector, build_vehicle_profile,
)
from gui.mods.offline_lan_0922.ai.navigation import (
    BAKED_SHALLOW_WATER, TerrainGrid, TerrainNavigator,
)
from gui.mods.offline_lan_0922 import prebaked_navigation
from gui.mods.offline_lan_0922.navigation_graph_schema import SUPPORTED_MAPS


class _StrictNoLegacyStuff(object):
    """Expose attributes while rejecting every mapping-style access path."""
    def __init__(self, **attributes):
        self.__dict__.update(attributes)

    @staticmethod
    def _forbidden(*unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    keys = _forbidden
    items = _forbidden
    values = _forbidden
    iterkeys = _forbidden
    iteritems = _forbidden
    itervalues = _forbidden
    __getitem__ = _forbidden
    __contains__ = _forbidden
    __iter__ = _forbidden
    __len__ = _forbidden


class BotAiPortTests(unittest.TestCase):
    @staticmethod
    def _formations():
        return {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        }

    @staticmethod
    def _baked_graph(width, height, blocked=()):
        directions = (
            (-1, -1), (0, -1), (1, -1),
            (-1, 0), (1, 0),
            (-1, 1), (0, 1), (1, 1),
        )
        blocked = set(blocked)
        heights = [0 if (x, z) not in blocked else None
                   for z in range(height) for x in range(width)]
        links = [0] * (width * height)
        for z in range(height):
            for x in range(width):
                index = z * width + x
                if heights[index] is None:
                    continue
                for direction_index, (dx, dz) in enumerate(directions):
                    next_x, next_z = x + dx, z + dz
                    if not (0 <= next_x < width and 0 <= next_z < height):
                        continue
                    if heights[next_z * width + next_x] is None:
                        continue
                    if dx and dz and (
                            heights[z * width + next_x] is None or
                            heights[next_z * width + x] is None):
                        continue
                    links[index] |= 1 << direction_index
        return {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (10.0, 20.0),
            'bounds': (8.0, 18.0, 10.0 + width * 4.0,
                       20.0 + height * 4.0),
            'width': width, 'height': height,
            'heights_mm': heights, 'links': links,
            'hazards': [0] * (width * height),
            'bake': {'max_grade': 0.30},
        }

    def test_graph_loader_uses_real_mod_config_filesystem(self):
        self.assertEqual(
            os.path.normpath('./mods/configs/offline_lan_0922'),
            os.path.normpath(prebaked_navigation.mod_dir()))

    def test_preserves_annotated_standard_maps(self):
        # The navigation schema is the exact #1513 standard-mode candidate set.
        # TACTICAL_MAPS also retains older annotations that are useful for
        # other supported clients, so it may be a strict superset.
        self.assertTrue(set(SUPPORTED_MAPS).issubset(set(maps.TACTICAL_MAPS)))
        karelia = maps.get_tactical_map('spaces/01_karelia')
        self.assertEqual('01_karelia', karelia['name'])
        self.assertTrue(karelia['routes'][1])
        self.assertTrue(karelia['routes'][2])

    def test_installs_all_user_reviewed_route_batches(self):
        reviewed = set(reviewed_routes_20260811.REVIEWED_ROUTE_POINTS)
        accepted = set(reviewed_routes_20260811.ACCEPTED_UNCHANGED_MAPS)
        self.assertEqual(38, len(reviewed))
        self.assertEqual(113, sum(
            len(routes) for routes in
            reviewed_routes_20260811.REVIEWED_ROUTE_POINTS.values()))
        self.assertEqual(
            {'34_redshire', '95_lost_city', '100_thepit'}, accepted)
        self.assertFalse(reviewed & accepted)

        original_maps = {'04_himmelsdorf': maps.HIMMELSDORF}
        for collection in (
                maps.bot_ai_maps_group_a.TACTICAL_MAPS_GROUP_A,
                maps.bot_ai_maps_group_b.TACTICAL_MAPS_GROUP_B,
                maps.bot_ai_maps_group_c.TACTICAL_MAPS_GROUP_C,
                maps.bot_ai_maps_extra.TACTICAL_MAPS_EXTRA,
                maps.bot_ai_maps_0922_extra.TACTICAL_MAPS_0922_EXTRA):
            original_maps.update(collection)
        for map_name in reviewed:
            self.assertIsNot(
                original_maps[map_name], maps.TACTICAL_MAPS[map_name],
                map_name)
        graph_maps = set(
            path.stem for path in (PORT_ROOT / 'navgraphs').glob('*.json')
            if path.name != 'manifest.json')
        self.assertEqual(graph_maps, reviewed | accepted)
        for map_name in accepted:
            self.assertIs(
                original_maps[map_name], maps.TACTICAL_MAPS[map_name],
                map_name)

        ensk = maps.TACTICAL_MAPS['06_ensk']
        for team in (1, 2):
            self.assertEqual(
                ['west_city', 'east_field'],
                [route['id'] for route in ensk['routes'][team]])
            self.assertEqual(
                [7, 7],
                [route['capacity'] for route in ensk['routes'][team]])

    def test_reviewed_route_geometry_is_bidirectional_and_single_gated(self):
        for map_name, reviewed_routes in sorted(
                reviewed_routes_20260811.REVIEWED_ROUTE_POINTS.items()):
            tactical = maps.TACTICAL_MAPS[map_name]
            team_routes = {}
            for team in (1, 2):
                team_routes[team] = dict(
                    (route['id'], route)
                    for route in tactical['routes'][team])
            with self.subTest(map=map_name):
                self.assertTrue(set(reviewed_routes).issubset(team_routes[1]))
                self.assertEqual(set(team_routes[1]), set(team_routes[2]))
                for route_id in reviewed_routes:
                    team_one = team_routes[1][route_id]['waypoints']
                    team_two = team_routes[2][route_id]['waypoints']
                    self.assertEqual(1, sum(
                        int(bool(point[2])) for point in team_one))
                    self.assertEqual(tuple(reversed(team_one)), team_two)
                    for key in ('capacity', 'risk', 'role_weights'):
                        self.assertEqual(
                            team_routes[1][route_id][key],
                            team_routes[2][route_id][key])

        himmelsdorf = maps.TACTICAL_MAPS['04_himmelsdorf']
        rear_one = next(route for route in himmelsdorf['routes'][1]
                        if route['id'] == 'rear_guard')
        rear_two = next(route for route in himmelsdorf['routes'][2]
                        if route['id'] == 'rear_guard')
        self.assertEqual(((-80.0, -270.0, 1),), rear_one['waypoints'])
        self.assertEqual(((45.0, 270.0, 1),), rear_two['waypoints'])

    def test_cover_contract_is_plain_data_and_deterministic(self):
        result = cover.score_candidates([{
            'id': 'ridge', 'position': (1, 2, 3), 'travel_distance': 5,
            'route_alignment': 1, 'enemy_occlusion': 1, 'exposure': 0,
            'slope': 0, 'water': 0, 'ally_congestion': 0,
            'peek_feasible': True, 'peek_position': (2, 2, 3),
            'escape_feasible': True,
        }])
        self.assertEqual('ridge', result[0]['id'])
        self.assertEqual({'x': 1.0, 'y': 2.0, 'z': 3.0}, result[0]['position'])

    def test_adapter_returns_no_engine_objects(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter('01_karelia', 7)
        adapter.register(1, 1, descriptor)
        order = adapter.decide({
            'id': 1, 'position': (0, 0, 0), 'yaw': 0, 'speed': 0,
            'dt': 0.05, 'now': 1, 'health': 100, 'max_health': 100,
            'contacts': (), 'neighbours': (),
        }, lambda yaw: True)
        self.assertEqual(1, order['bot_id'])
        self.assertIn('throttle', order)
        self.assertIsInstance(order['move_position'], tuple)

    def test_vehicle_profile_reads_native_1513_components_as_attributes(self):
        descriptor = _StrictNoLegacyStuff(
            type=_StrictNoLegacyStuff(
                name='Strict heavy', tags=('heavyTank',)),
            physics={'speedLimits': (18.0, 7.0)},
            hull=_StrictNoLegacyStuff(primaryArmor=(110.0, 135.0, 95.0)),
            turret=_StrictNoLegacyStuff(primaryArmor=180.0),
            gun=_StrictNoLegacyStuff(shots=(
                _StrictNoLegacyStuff(
                    speed=900.0,
                    shell=_StrictNoLegacyStuff(
                        kind='ARMOR_PIERCING',
                        piercingPower=(150.0, 170.0),
                        damage=(300.0, 340.0))),
                _StrictNoLegacyStuff(
                    speed=640.0,
                    shell=_StrictNoLegacyStuff(
                        kind='HIGH_EXPLOSIVE',
                        piercingPower=(42.0, 58.0),
                        damage=(410.0, 450.0))),
            )),
        )

        profile = build_vehicle_profile(descriptor)

        self.assertEqual('heavyTank', profile['class_tag'])
        self.assertEqual('Strict heavy', profile['vehicle_name'])
        self.assertEqual(18.0, profile['speed'])
        self.assertEqual(180.0, profile['armor'])
        self.assertEqual((
            {'index': 0, 'kind': 'ARMOR_PIERCING',
             'penetration': 160.0, 'damage': 320.0, 'speed': 900.0},
            {'index': 1, 'kind': 'HIGH_EXPLOSIVE',
             'penetration': 50.0, 'damage': 430.0, 'speed': 640.0},
        ), profile['shells'])

    @staticmethod
    def _strategy_profile(class_tag, speed, armor):
        return build_vehicle_profile({
            'type': {'name': class_tag, 'tags': (class_tag,)},
            'physics': {'speedLimits': (speed, 6.0)},
            'hull': {'primaryArmor': armor},
            'turret': {'primaryArmor': armor},
            'gun': {'shots': ()},
        })

    def test_karelia_strategy_matches_vehicle_classes_from_both_spawns(self):
        profiles = {
            'H': self._strategy_profile('heavyTank', 12.0, 180.0),
            'L': self._strategy_profile('lightTank', 20.0, 35.0),
            'M': self._strategy_profile('mediumTank', 18.0, 85.0),
            'T': self._strategy_profile('AT-SPG', 11.0, 110.0),
            'S': self._strategy_profile('SPG', 10.0, 30.0),
        }
        expected = {
            'H': 'east_shelf',
            'L': 'middle_road',
            'M': 'west_ridge',
            'T': 'west_ridge',
            'S': 'middle_road',
        }

        for team in (1, 2):
            for label, profile in profiles.items():
                director = BattleDirector('01_karelia', 41)
                agent = director.register_profile(
                    100 + team, team, profile, label)
                with self.subTest(team=team, vehicle_class=label):
                    self.assertEqual(expected[label], agent['route']['id'])

    def test_karelia_common_lineup_orders_keep_primary_class_lanes(self):
        profiles = {
            'H': self._strategy_profile('heavyTank', 12.0, 180.0),
            'L': self._strategy_profile('lightTank', 20.0, 35.0),
            'M': self._strategy_profile('mediumTank', 18.0, 85.0),
            'T': self._strategy_profile('AT-SPG', 11.0, 110.0),
            'S': self._strategy_profile('SPG', 10.0, 30.0),
        }
        expected = {
            'H': 'east_shelf', 'L': 'middle_road',
            'M': 'west_ridge', 'T': 'west_ridge',
            'S': 'middle_road',
        }
        lineups = (
            'HHHHHLLLLMMMTTS',
            'LLLLMMMTTSHHHHH',
        )

        for team in (1, 2):
            for lineup in lineups:
                director = BattleDirector('01_karelia', 73)
                assigned = []
                for index, label in enumerate(lineup):
                    agent = director.register_profile(
                        index + 1, team, profiles[label],
                        '%s-%d' % (label, index))
                    assigned.append((label, agent['route']['id']))
                with self.subTest(team=team, lineup=lineup):
                    self.assertEqual(
                        [(label, expected[label]) for label in lineup],
                        assigned)
                    # The SPG uses a rear staging anchor on middle_road without
                    # consuming one of its four front-line slots.
                    self.assertEqual(
                        4, director.route_usage[(team, 'middle_road')])

    def test_spg_uses_battery_route_without_consuming_frontline_capacity(self):
        director = BattleDirector('04_himmelsdorf', 29)
        profile = self._strategy_profile('SPG', 10.0, 30.0)

        first = director.register_profile(501, 1, profile, 'Battery one')
        second = director.register_profile(502, 1, profile, 'Battery two')

        self.assertEqual('rear_guard', first['route']['id'])
        self.assertNotEqual(first['route']['id'], second['route']['id'])
        self.assertEqual({}, director.route_usage)

    def test_baked_route_geometry_uses_current_static_strategy_metadata(self):
        baked = {'1': [], '2': []}
        route_ids = ('west_ridge', 'middle_road', 'east_shelf')
        for team in (1, 2):
            for index, route_id in enumerate(route_ids):
                baked[str(team)].append({
                    'id': route_id,
                    'capacity': 1,
                    'risk': 0.01,
                    'role_weights': {'scout': 0.0},
                    'waypoints': (
                        (float(index), float(team), False),
                        (float(index + 10), float(team), True),
                    ),
                })
            baked[str(team)].append({
                'id': 'custom_route',
                'capacity': 9,
                'risk': 0.33,
                'role_weights': {'support': 0.77},
                'waypoints': ((20.0, float(team), False),),
            })

        director = BattleDirector(
            '01_karelia', 19, baked_routes=baked)

        for team in (1, 2):
            routes = dict((route['id'], route)
                          for route in director._routes_for(team))
            with self.subTest(team=team):
                self.assertEqual(
                    baked[str(team)][1]['waypoints'],
                    routes['middle_road']['waypoints'])
                self.assertEqual(6, routes['east_shelf']['capacity'])
                self.assertEqual(
                    1.0, routes['middle_road']['role_weights']['scout'])
                self.assertEqual(
                    1.0,
                    routes['middle_road']['class_weights']['lightTank'])
                self.assertEqual(9, routes['custom_route']['capacity'])
                self.assertEqual(
                    {'support': 0.77},
                    routes['custom_route']['role_weights'])

    def test_adapter_preserves_face_and_commanded_hold_semantics(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter('01_karelia', 7)
        adapter.register(1, 1, descriptor)
        order = adapter.decide_with_order({
            'id': 1, 'position': (0.0, 0.0, 0.0), 'yaw': 0.0,
            'speed': 0.0, 'dt': 0.05, 'now': 1.0,
            'neighbours': (),
        }, {
            'target_id': 2,
            'aim_position': (0.0, 0.0, 50.0),
            'move_position': (0.0, 0.0, 0.0),
            'face_position': (20.0, 0.0, 40.0),
            'fire_allowed': True, 'fire_range': 400.0,
            'combat_mode': 'cover_hold', 'shell_index': 0,
            'throttle_override': 0.0,
        }, lambda unused_yaw: True)

        self.assertEqual((20.0, 0.0, 40.0), order['face_position'])
        self.assertFalse(order['movement_intent'])
        self.assertEqual(0.0, order['throttle'])
        self.assertGreater(order['turn'], 0.0)
        self.assertAlmostEqual(math.atan2(20.0, 40.0), order['target_yaw'])

    def test_local_director_does_not_jiggle_without_confirmed_cover(self):
        descriptor = {
            'type': {'name': 'heavy', 'tags': ('heavyTank',)},
            'physics': {'speedLimits': (12.0,)},
            'hull': {'primaryArmor': 180.0},
            'turret': {'primaryArmor': 180.0,
                       'circularVisionRadius': 400.0},
            'gun': {'shots': ()},
        }
        director = BattleDirector('04_himmelsdorf', 45)
        agent = director.register(401, 1, descriptor, 'Jiggler')
        agent['personality'].update({
            'caution': 0.2, 'patience': 0.2,
            'aggression': 0.3, 'jiggle': 0.95,
        })
        position = (185.0, 0.0, -82.0)
        target = (185.0, 0.0, -22.0)
        modes = set()
        throttle_values = set()

        for tick in range(600):
            now = tick * 0.2
            director.update_contact(
                1, 402, 2, target, 1000, 1000,
                'heavyTank', True, now)
            order = director.order_for(
                401, position, 0.0, 1000, 1000, now)
            modes.add(order['combat_mode'])
            throttle_values.add(order['throttle_override'])

        self.assertNotIn('jiggle_forward', modes)
        self.assertNotIn('jiggle_back', modes)
        self.assertEqual({None}, throttle_values)

    def test_discovered_artillery_has_priority_over_a_soft_regular_target(self):
        director = BattleDirector('04_himmelsdorf', 46)
        profile = self._strategy_profile('mediumTank', 18.0, 85.0)
        director.register_profile(401, 1, profile, 'Hunter')
        director.update_contact(
            1, 402, 2, (0.0, 0.0, 100.0), 10, 1000,
            'heavyTank', True, 1.0, shootable_by_ids=(401,))
        director.update_contact(
            1, 403, 2, (0.0, 0.0, 130.0), 500, 500,
            'SPG', True, 1.0, shootable_by_ids=(401,))

        order = director.order_for(
            401, (0.0, 0.0, 0.0), 0.0, 1000, 1000, 1.0)

        self.assertEqual(403, order['target_id'])

    def test_unspotted_artillery_does_not_override_a_visible_target(self):
        director = BattleDirector('04_himmelsdorf', 47)
        profile = self._strategy_profile('mediumTank', 18.0, 85.0)
        director.register_profile(401, 1, profile, 'Hunter')
        director.update_contact(
            1, 402, 2, (0.0, 0.0, 100.0), 800, 1000,
            'heavyTank', True, 1.0, shootable_by_ids=(401,))
        director.update_contact(
            1, 403, 2, (0.0, 0.0, 130.0), 500, 500,
            'SPG', False, 1.0, shootable_by_ids=(401,))

        order = director.order_for(
            401, (0.0, 0.0, 0.0), 0.0, 1000, 1000, 1.0)

        self.assertEqual(402, order['target_id'])

    def test_navigation_accepts_caller_probes(self):
        grid = TerrainGrid(lambda x, z, hint_y: 0.0,
                           bounds=(-50, -50, 50, 50))
        path = grid.plan((0, 0, 0), (30, 0, 30))
        self.assertTrue(path)

    def test_baked_graph_uses_immutable_links_without_runtime_probes(self):
        # The 8-way link bits match the shipped graph contract: east is bit 4
        # and west is bit 3. A runtime probe that raises proves the baked
        # geometry rather than an accidental fallback supplies the path.
        graph = {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (0.0, 0.0), 'bounds': (0, 0, 8, 0),
            'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
            'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
            'hazards': (0, 0, 0),
            'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
            'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
            'spawn_formations': self._formations(),
            'routes': {
                '1': ({'id': 'lane', 'waypoints': (
                    (0.0, 0.0, False), (8.0, 0.0, False))},),
                '2': ({'id': 'lane', 'waypoints': (
                    (8.0, 0.0, False), (0.0, 0.0, False))},),
            },
            'bake': {'max_grade': 0.30},
        }
        self.assertIs(prebaked_navigation._validate(graph, '01_karelia'), graph)
        grid = TerrainGrid(lambda *unused: (_ for _ in ()).throw(AssertionError()),
                           baked_graph=graph)
        self.assertTrue(grid.prebaked)
        path = grid.plan((0, 0, 0), (8, 0, 0))
        self.assertEqual((0.0, 0.0, 0.0), path[0])
        self.assertEqual((8.0, 0.0, 0.0), path[-1])

    def test_prebaked_shortcuts_prefer_dry_detours(self):
        graph = self._baked_graph(5, 3)
        graph['hazards'] = [
            0, 4, 4, 4, 0,
            0, 0, 0, 0, 0,
            0, 0, 0, 0, 0,
        ]
        navigator = TerrainNavigator(lambda *unused: None,
                                     baked_graph=graph)
        start = (10.0, 0.0, 20.0)
        goal = (26.0, 0.0, 20.0)

        unused_key, path = navigator._path(
            ('route', 1, 'dry-detour'), start, goal, 1.0, None)

        self.assertTrue(navigator.grid.segment_has_baked_hazard(
            start, goal, BAKED_SHALLOW_WATER))
        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 20.0)
        for first, second in zip(path, path[1:]):
            self.assertFalse(navigator.grid.segment_has_baked_hazard(
                first, second, BAKED_SHALLOW_WATER))

        selected = navigator.next_target(
            7, start, goal, ('route', 1, 'dry-detour'), 1.1)

        self.assertNotEqual(goal, selected)
        self.assertFalse(navigator.grid.segment_has_baked_hazard(
            start, selected, BAKED_SHALLOW_WATER))
        self.assertFalse(navigator.controlled_shallow_step(
            7, start, math.pi * 0.5))

    def test_reached_dry_corner_does_not_authorize_offset_shallow_shortcut(self):
        graph = self._baked_graph(4, 3)
        graph['hazards'][5] = 4
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        current = (11.9, 0.0, 20.0)
        first = (10.0, 0.0, 20.0)
        corner = (14.0, 0.0, 20.0)
        goal = (22.0, 0.0, 28.0)
        path = (first, corner, goal)
        navigator._path = lambda *unused: (('fixed-path',), path)

        self.assertTrue(navigator.grid.segment_has_baked_hazard(
            current, goal, BAKED_SHALLOW_WATER))
        self.assertFalse(navigator.grid.segment_has_baked_hazard(
            corner, goal, BAKED_SHALLOW_WATER))

        selected = navigator.next_target(
            7, current, goal, ('route', 1, 'dry-corners'), 1.0)

        self.assertEqual(corner, selected)
        self.assertFalse(navigator.controlled_shallow_step(
            7, current, math.atan2(
                goal[0] - current[0], goal[2] - current[2])))

    def test_prebaked_astar_prefers_dry_route_over_short_shallow_crossing(self):
        graph = self._baked_graph(5, 3)
        graph['hazards'] = [
            0, 4, 4, 4, 0,
            0, 0, 0, 0, 0,
            0, 0, 0, 0, 0,
        ]
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)
        grid._smooth = lambda path, *unused, **unused_keywords: path

        path = grid.plan(
            (10.0, 0.0, 20.0), (26.0, 0.0, 20.0),
            prefer_clearance=False)

        self.assertTrue(path)
        self.assertGreater(max(point[2] for point in path), 20.0)

    def test_prebaked_smoothing_and_local_recovery_do_not_enter_shallow_water(self):
        graph = self._baked_graph(5, 5)
        graph['hazards'] = [4] * 25
        graph['hazards'][2 * 5 + 2] = 0
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)
        start = (18.0, 0.0, 28.0)
        middle = (22.0, 0.0, 28.0)
        goal = (26.0, 0.0, 28.0)

        self.assertEqual(
            (start, middle, goal),
            grid._smooth((start, middle, goal), prefer_clearance=False))
        local = grid.safe_local_target(start, goal, 1.0)
        self.assertIsNotNone(local)
        self.assertFalse(grid.segment_has_baked_hazard(
            start, local, BAKED_SHALLOW_WATER))

    def test_prebaked_hazard_check_allows_tank_to_leave_shallow_water(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [4, 0, 0]
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)

        self.assertTrue(grid.point_has_baked_hazard(
            (10.0, 0.0, 20.0), BAKED_SHALLOW_WATER))
        self.assertFalse(grid.segment_has_baked_hazard(
            (10.0, 0.0, 20.0), (18.0, 0.0, 20.0),
            BAKED_SHALLOW_WATER))

    def test_pending_and_failed_searches_do_not_claim_shallow_direct_goal(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, 4, 0]
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)

        for path_result in (None, ()):
            navigator = TerrainNavigator(
                lambda *unused: None, baked_graph=graph)
            navigator._path = lambda *unused, result=path_result: (
                ('search-result',), result)
            navigator.grid.safe_local_target = lambda *unused: None

            selected = navigator.next_target(
                7, current, goal, ('route', 1, 'wet-shortcut'), 1.0)

            with self.subTest(path_result=path_result):
                self.assertEqual(goal, selected)
                self.assertEqual('reactive', navigator.fallback_modes[7])
                self.assertFalse(navigator.controlled_shallow_step(
                    7, current, math.pi * 0.5))

    def test_unavoidable_astar_shallow_edge_authorizes_only_planned_heading(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, 4, 0]
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)

        navigator.next_target(
            7, current, goal, ('route', 1, 'only-ford'), 1.0)
        selected = navigator.next_target(
            7, current, goal, ('route', 1, 'only-ford'), 1.1)

        self.assertEqual((14.0, 0.0, 20.0), selected)
        self.assertNotIn(7, navigator.fallback_modes)
        self.assertTrue(navigator.controlled_shallow_step(
            7, current, math.pi * 0.5))
        self.assertFalse(navigator.controlled_shallow_step(
            7, current, math.pi * 0.5 + 0.42))

    def test_navigation_housekeeping_runs_once_per_second(self):
        navigator = TerrainNavigator(lambda *unused: 0.0)
        calls = []
        navigator.grid.prune_failed_edges = (
            lambda now: calls.append(('prune', now)))
        navigator.grid.trim_caches = lambda: calls.append(('trim', None))

        navigator.tick(1.0)
        navigator.tick(1.0)
        navigator.tick(1.5)
        navigator.tick(2.1)

        self.assertEqual([
            ('prune', 1.0), ('trim', None),
            ('prune', 2.1), ('trim', None),
        ], calls)

    def test_empty_failed_edge_table_skips_route_segment_scans(self):
        grid = TerrainGrid(lambda *unused: 0.0)

        def unexpected_scan(*unused):
            raise AssertionError('empty failed-edge table scanned a route')

        grid._edge_keys_for_segment = unexpected_scan
        self.assertEqual(0.0, grid.segment_penalty(
            (0.0, 0.0, 0.0), (20.0, 0.0, 0.0), 1.0))
        self.assertFalse(grid.path_has_penalty((
            (0.0, 0.0, 0.0), (20.0, 0.0, 0.0)), 1.0))

        edge = ((0, 0), (1, 0))
        grid._failed_edges[edge] = (10.0, 7.0)
        grid._edge_keys_for_segment = lambda *unused: (edge,)
        self.assertEqual(7.0, grid.segment_penalty(
            (0.0, 0.0, 0.0), (4.0, 0.0, 0.0), 1.0))
        self.assertTrue(grid.path_has_penalty((
            (0.0, 0.0, 0.0), (4.0, 0.0, 0.0)), 1.0))

    def test_superseded_route_join_search_is_cancelled_for_its_bot(self):
        navigator = TerrainNavigator(lambda *unused: 0.0)
        route_join = (('route_join', 11, 2, 'forest', 1), (4, 5))
        other_bot = (('route_join', 12, 2, 'forest', 1), (4, 5))
        navigator.searches[route_join] = object()
        navigator.searches[other_bot] = object()
        navigator.search_times[route_join] = 1.0
        navigator.search_times[other_bot] = 1.0

        navigator._cancel_bot_searches(11)

        self.assertNotIn(route_join, navigator.searches)
        self.assertNotIn(route_join, navigator.search_times)
        self.assertIn(other_bot, navigator.searches)
        self.assertIn(other_bot, navigator.search_times)

    def test_graph_validation_rejects_incomplete_battle_contract(self):
        graph = {
            'format': 'offline-lan-0922-navgraph', 'version': 2,
            'game_version': '0.9.22.0.1-cn-1513', 'map': '01_karelia',
            'cell_size': 4.0, 'origin': (0.0, 0.0),
            'bounds': (0.0, 0.0, 8.0, 0.0),
            'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
            'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
            'hazards': (0, 0, 0),
            'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
            'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
            'spawn_formations': self._formations(),
            'routes': {
                '1': ({'id': 'lane', 'waypoints': (
                    (0.0, 0.0, False), (8.0, 0.0, False))},),
                '2': ({'id': 'lane', 'waypoints': (
                    (8.0, 0.0, False), (0.0, 0.0, False))},),
            },
        }

        cases = {
            'format': lambda value: value.update(format='wrong'),
            'version': lambda value: value.update(version=1),
            'map': lambda value: value.update(map='02_malinovka'),
            'grid_array': lambda value: value.update(hazards=(0,)),
            'team_routes': lambda value: value['routes'].update({'2': ()}),
            'route_length': lambda value: value['routes']['1'][0].update(
                waypoints=((0.0, 0.0, False),)),
            'spawn_anchors': lambda value: value.update(spawn_anchors=()),
            'objective_bases': lambda value: value.update(objective_bases=()),
            'spawn_formations': lambda value: value.update(
                spawn_formations={}),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                invalid = copy.deepcopy(graph)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    prebaked_navigation._validate(invalid, '01_karelia')

        graph['game_version'] = 'locally-repacked-client'
        self.assertIs(graph, prebaked_navigation._validate(
            graph, '01_karelia'))

    def test_baked_routes_keep_validated_team_endpoints_unchanged(self):
        team_one = ((-2.0, -350.0, False),
                    (40.0, -120.0, True),
                    (-2.0, 350.0, False))
        team_two = tuple(reversed(team_one))
        director = BattleDirector('95_lost_city', 7, baked_routes={
            '1': ({'id': 'baked-1', 'waypoints': team_one},),
            '2': ({'id': 'baked-2', 'waypoints': team_two},),
        })

        first = director.register_profile(1, 1, {'roles': {}}, 'First')
        second = director.register_profile(2, 2, {'roles': {}}, 'Second')

        self.assertEqual(team_one, first['route']['waypoints'])
        self.assertEqual(team_two, second['route']['waypoints'])
        self.assertEqual(3, len(first['route']['waypoints']))

    def test_route_hold_metadata_does_not_pause_local_director(self):
        director = BattleDirector('07_lakeville', 'no-route-holds')
        agent = director.register_profile(
            92, 1, {'roles': {}, 'vehicle_name': 'medium'},
            'Continuous traveller')
        agent['route'] = {
            'waypoints': (
                (0.0, -40.0, False),
                (0.0, -20.0, True),
                (0.0, 80.0, False),
            )}
        agent['route_started'] = True
        agent['waypoint_index'] = 1

        target = director._route_position(agent, (0.0, 0.0, -20.0), 3.0)

        self.assertEqual((0.0, 0.0, 80.0), target)
        self.assertEqual(2, agent['waypoint_index'])

    def test_spawn_skips_rear_connector_and_anchors_navigation_at_hull(self):
        director = BattleDirector('07_lakeville', 'forward-join')
        descriptor = {
            'type': {'name': 'medium', 'tags': ('mediumTank',)},
            'physics': {'speedLimits': (18.0,)}, 'hull': {},
            'turret': {}, 'gun': {'shots': ()}}
        agent = director.register(
            93, 1, descriptor, 'Forward join bot')
        agent['route'] = {
            'waypoints': (
                (0.0, -40.0, False),
                (0.0, -65.0, False),
                (40.0, 40.0, False),
            )}
        agent['route_started'] = False

        order = director.order_for(
            93, (0.0, 0.0, -20.0), 0.0, 1000, 1000, 0.0)

        self.assertEqual((40.0, 0.0, 40.0), order['move_position'])
        self.assertEqual(2, agent['waypoint_index'])
        self.assertEqual((0.0, 0.0, -20.0), order['route_anchor'])

    def test_normal_route_turn_keeps_full_throttle(self):
        driver = LocalDriver()
        target_yaw = 0.9
        order = driver.drive(
            2, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (math.sin(target_yaw) * 50.0, 0.0,
             math.cos(target_yaw) * 50.0),
            (), lambda unused_angle: True)

        self.assertEqual('drive', order['recovery_mode'])
        self.assertEqual(1.0, order['throttle'])
        self.assertGreater(order['turn'], 0.9)

    def test_same_lane_vehicle_does_not_replace_route_with_predictive_stop(self):
        driver = LocalDriver()
        neighbour = {
            'position': (0.0, 0.0, 10.0),
            'yaw': 0.0,
            'velocity': (0.0, 0.0, 1.0),
            'half_length': 3.5,
            'half_width': 1.7,
        }

        order = driver.drive(
            133, (0.0, 0.0, 0.0), 0.0, 8.0, 0.1,
            (0.0, 0.0, 50.0), (neighbour,),
            lambda unused_yaw: True,
            velocity=(0.0, 0.0, 8.0),
            half_length=3.5, half_width=1.7)

        self.assertEqual('drive', order['recovery_mode'])
        self.assertEqual(1.0, order['throttle'])
        self.assertAlmostEqual(0.0, order['target_yaw'])

    def test_slow_callback_advances_the_complete_planner_interval(self):
        driver = LocalDriver()

        driver.drive(
            3, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)

        state = driver.states[3]
        self.assertAlmostEqual(1.0, state['last_step'])
        self.assertAlmostEqual(1.0, state['clock'])

    def test_large_route_turn_pivots_before_driving_and_never_reverses(self):
        # A target far behind the bow commands a stationary pivot. Rotation is
        # progress, so the driver must not reinterpret it as a stuck recovery.
        driver = LocalDriver()
        yaw = 0.0
        modes = set()
        throttles = set()
        for unused in range(150):
            order = driver.drive(
                7, (0.0, 0.0, 0.0), yaw, 0.0, 1.0 / 30.0,
                (0.0, 0.0, -50.0), (), lambda unused_yaw: True)
            modes.add(order['recovery_mode'])
            throttles.add(order['throttle'])
            yaw += order['turn'] * 0.66 * (1.0 / 30.0)

        self.assertNotIn('reverse_turn', modes)
        self.assertNotIn('pivot_recovery', modes)
        self.assertIn(0.0, throttles)
        self.assertIn(1.0, throttles)
        self.assertGreater(abs(yaw), 2.5)

    def test_prohorovka_west_ridge_corner_keeps_forward_progress(self):
        driver = LocalDriver()
        graph = json.loads(
            (PORT_ROOT / 'navgraphs' / '05_prohorovka.json').read_text())
        route = next(
            route for route in graph['routes']['1']
            if route['id'] == 'west_ridge')
        corner_index = next(
            index for index, point in enumerate(route['waypoints'])
            if tuple(point[:2]) == (54.0, -442.0))
        first, corner, target = route['waypoints'][corner_index - 1:
                                                   corner_index + 2]
        incoming_yaw = math.atan2(
            float(corner[0]) - float(first[0]),
            float(corner[1]) - float(first[1]))

        order = driver.drive(
            121, (corner[0], 0.0, corner[1]), incoming_yaw, 0.0, 0.1,
            (target[0], 0.0, target[1]),
            (), lambda unused_yaw: True)

        self.assertEqual('drive', order['recovery_mode'])
        self.assertEqual(1.0, order['throttle'])
        self.assertGreater(abs(order['turn']), 0.9)

    def test_a_wedged_hull_still_reaches_recovery(self):
        driver = LocalDriver()
        modes = set()
        for unused in range(150):
            order = driver.drive(
                8, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0 / 30.0,
                (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
            modes.add(order['recovery_mode'])

        self.assertTrue(modes & set(('reverse_turn', 'pivot_recovery')))

    def test_a_wedged_hull_near_a_waypoint_still_reaches_recovery(self):
        driver = LocalDriver()
        modes = set()
        for unused in range(150):
            order = driver.drive(
                82, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0 / 30.0,
                (0.0, 0.0, 2.0), (), lambda unused_yaw: True)
            modes.add(order['recovery_mode'])

        self.assertTrue(modes & set(('reverse_turn', 'pivot_recovery')))

    def test_brief_traffic_wait_does_not_trigger_reverse_recovery(self):
        driver = LocalDriver()
        order = None
        for unused in range(10):
            order = driver.drive(
                130, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
            driver.wait_for_traffic(130)

        state = driver.states[130]
        self.assertEqual('drive', order['recovery_mode'])
        self.assertEqual((0.0, 0.0), (
            state['stuck_time'], state['recovery_time']))

    def test_continuous_traffic_wait_eventually_allows_recovery(self):
        driver = LocalDriver()
        recovery = None
        for unused in range(80):
            order = driver.drive(
                131, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
            if order['recovery_mode'] in (
                    'reverse_turn', 'pivot_recovery'):
                recovery = order
                break
            driver.wait_for_traffic(131)

        self.assertIsNotNone(recovery)
        self.assertGreater(
            driver.states[131]['traffic_wait_time'], 1.5)

    def test_moving_between_traffic_waits_renews_the_brief_lease(self):
        driver = LocalDriver()
        for unused in range(10):
            driver.drive(
                132, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
                (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
            driver.wait_for_traffic(132)

        driver.drive(
            132, (1.0, 0.0, 0.0), 0.0, 2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
        order = driver.drive(
            132, (1.2, 0.0, 0.0), 0.0, 2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
        driver.wait_for_traffic(132)

        state = driver.states[132]
        self.assertEqual('drive', order['recovery_mode'])
        self.assertAlmostEqual(0.1, state['traffic_wait_time'])

    def test_wall_avoidance_commits_to_one_clear_branch(self):
        driver = LocalDriver()

        def clear(yaw):
            return abs(yaw) > 0.20

        first = driver.drive(
            81, (0.0, 0.0, 0.0), 0.0, 3.0, 0.05,
            (0.0, 0.0, 50.0), (), clear)
        shifted_yaw = 1.30
        second = driver.drive(
            81, (0.0, 0.0, 0.1), 0.1, 3.0, 0.05,
            (math.sin(shifted_yaw) * 50.0, 0.0,
             math.cos(shifted_yaw) * 50.0), (), clear)

        self.assertEqual('avoid', first['recovery_mode'])
        self.assertAlmostEqual(
            first['target_yaw'], second['target_yaw'], places=6)

    def test_repeated_obstacle_failures_widen_on_one_side(self):
        driver = LocalDriver(failure_ttl=5.0)
        first = driver.drive(
            17, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
        driver.remember_failure(17, first['target_yaw'], ttl=5.0)
        second = driver.drive(
            17, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
        driver.remember_failure(17, second['target_yaw'], ttl=5.0)
        third = driver.drive(
            17, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)

        self.assertEqual('avoid', second['recovery_mode'])
        self.assertEqual('avoid', third['recovery_mode'])
        self.assertGreater(second['target_yaw'] * third['target_yaw'], 0.0)
        self.assertGreater(abs(third['target_yaw']),
                           abs(second['target_yaw']))

    def test_adjacent_bots_choose_opposite_initial_escape_sides(self):
        driver = LocalDriver(failure_ttl=5.0)
        escaped = []
        for bot_id in (20, 21):
            straight = driver.drive(
                bot_id, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
                (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
            driver.remember_failure(
                bot_id, straight['target_yaw'], ttl=5.0)
            escaped.append(driver.drive(
                bot_id, (0.0, 0.0, 0.0), 0.0, 2.0, 0.1,
                (0.0, 0.0, 50.0), (),
                lambda unused_yaw: True)['target_yaw'])

        self.assertLess(escaped[0] * escaped[1], 0.0)

    def test_uphill_route_turn_aligns_before_drive_torque(self):
        driver = LocalDriver()
        uphill = driver.drive(
            120, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (20.0, 6.0, 20.0), (), lambda unused_yaw: True)
        flat = driver.drive(
            121, (0.0, 0.0, 0.0), 0.0, 0.0, 0.1,
            (20.0, 0.0, 20.0), (), lambda unused_yaw: True)

        self.assertEqual(0.0, uphill['throttle'])
        self.assertGreater(abs(uphill['turn']), 0.9)
        self.assertEqual(1.0, flat['throttle'])


if __name__ == '__main__':
    unittest.main()
