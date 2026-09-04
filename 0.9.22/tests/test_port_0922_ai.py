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
sys.path.insert(0, str(PORT_ROOT / 'server'))
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.ai import (
    cover, maps, navigation, reviewed_routes_20260811,
)
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import (
    LocalDriver, TRAFFIC_WAIT_LEASE_SECONDS,
)
from gui.mods.offline_lan_0922.ai.planner import (
    BattleDirector, build_vehicle_profile,
)
from gui.mods.offline_lan_0922.ai.navigation import (
    BAKED_FATAL_HAZARDS, BAKED_SHALLOW_WATER, _distance_2d,
    MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME,
    MAX_SEARCH_EXPANSIONS_PER_FRAME, SEARCH_EXPANSIONS_PER_SECOND,
    TerrainGrid, TerrainNavigator,
)
from gui.mods.offline_lan_0922 import prebaked_navigation
from lan_battle_server import MAP_POOL


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


class _PendingSearch(object):
    """A* job double that counts expansions and never completes."""
    def __init__(self):
        self.done = False
        self.last_frame = None
        self.steps = 0

    def step(self, count):
        self.steps += int(count)


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
        # The server pool is the exact #1513 standard-mode candidate set.
        # TACTICAL_MAPS also retains older annotations that are useful for
        # other supported clients, so it may be a strict superset.
        self.assertTrue(set(MAP_POOL).issubset(set(maps.TACTICAL_MAPS)))
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

        malinovka = maps.TACTICAL_MAPS['02_malinovka']
        for team in (1, 2):
            self.assertEqual(
                ['west_lake_road', 'central_field', 'east_hill_loop'],
                [route['id'] for route in malinovka['routes'][team]])
            self.assertEqual(
                [3, 5, 6],
                [route['capacity'] for route in malinovka['routes'][team]])

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

    def test_adapter_reports_pending_navigation_as_wait_not_arrival(self):
        descriptor = {'type': {'name': 'MS-1', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        adapter = BotAdapter(
            '01_karelia', 7,
            navigation_target=lambda unused_id, position, *unused: position)
        adapter.register(1, 1, descriptor)

        order = adapter.decide_with_order({
            'id': 1, 'position': (0.0, 0.0, 0.0), 'yaw': 0.25,
            'speed': 0.0, 'dt': 0.05, 'now': 1.0,
            'neighbours': (),
        }, {
            'move_position': (0.0, 0.0, 100.0),
            'fire_allowed': False, 'fire_range': 400.0,
            'combat_mode': 'route', 'shell_index': 0,
        }, lambda unused_yaw: self.fail(
            'pending navigation must not probe movement corridors'))

        self.assertEqual((0.0, 0.0, 0.0), order['move_position'])
        self.assertEqual('nav_wait', order['recovery_mode'])
        self.assertEqual(0.0, order['throttle'])
        self.assertLess(order['turn'], 0.0)
        self.assertEqual(0.0, order['target_yaw'])

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

    def test_pending_shallow_search_holds_without_arming_recovery(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, 4, 0]
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        navigator._path = lambda *unused: (('search-result',), None)
        navigator.grid.safe_local_target = lambda *unused: None

        selected = navigator.next_target(
            7, current, goal, ('route', 1, 'wet-shortcut'), 1.0)

        self.assertEqual(current, selected)
        self.assertEqual('pending', navigator.fallback_modes[7])
        self.assertTrue(TerrainNavigator.navigation_paused(
            current, goal, selected, minimum_request_distance=5.0))
        self.assertFalse(navigator.controlled_shallow_step(
            7, current, math.pi * 0.5))

    def _pending_navigator(self):
        """A navigator whose global search never finishes this frame."""
        graph = self._baked_graph(9, 3)
        hazards = [0] * 27
        for row in range(3):
            hazards[row * 9 + 5] = BAKED_SHALLOW_WATER
        graph['hazards'] = hazards
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        navigator._path = lambda *unused: (('search-result',), None)
        return navigator

    def test_pending_search_holds_only_for_a_bounded_grace(self):
        """A queued search must not park the hull for the whole search.

        The room shares one expansion budget, so a pending job can outlive
        several seconds of real time. Holding briefly avoids a pointless creep
        when the job finishes in a frame or two; holding forever is a parked
        tank that the driver reads as arrival and the order adapter refuses to
        steer at all.
        """
        navigator = self._pending_navigator()
        current = (10.0, 0.0, 24.0)
        goal = (42.0, 0.0, 24.0)
        path_key = ('route_join', 7, 1, 'lane', 1)

        held = navigator.next_target(7, current, goal, path_key, 1.0)
        self.assertEqual(current, held)
        self.assertEqual('pending', navigator.fallback_modes[7])

        moved = navigator.next_target(
            7, current, goal, path_key,
            1.0 + navigation.PENDING_PROGRESS_SECONDS)

        self.assertNotEqual(current, moved)
        self.assertLess(_distance_2d(moved, goal), _distance_2d(current, goal))
        self.assertFalse(navigator.grid.segment_has_baked_hazard(
            current, moved, BAKED_FATAL_HAZARDS | BAKED_SHALLOW_WATER))
        self.assertTrue(navigator.grid.segment_clear(current, moved))
        self.assertFalse(TerrainNavigator.navigation_paused(
            current, goal, moved, minimum_request_distance=5.0))

    def test_pending_search_still_holds_when_no_safe_step_exists(self):
        """Bounded progress never invents a step the probes did not prove."""
        navigator = self._pending_navigator()
        navigator.grid.safe_local_target = lambda *unused: None
        current = (10.0, 0.0, 24.0)
        goal = (42.0, 0.0, 24.0)
        path_key = ('route_join', 7, 1, 'lane', 1)

        navigator.next_target(7, current, goal, path_key, 1.0)
        held = navigator.next_target(
            7, current, goal, path_key,
            1.0 + navigation.PENDING_PROGRESS_SECONDS)

        self.assertEqual(current, held)
        self.assertEqual('pending', navigator.fallback_modes[7])

    def test_completed_route_target_restarts_the_pending_grace(self):
        """A real routed target ends the episode; a probed step does not."""
        navigator = self._pending_navigator()
        current = (10.0, 0.0, 24.0)
        goal = (42.0, 0.0, 24.0)
        path_key = ('route_join', 7, 1, 'lane', 1)

        navigator.next_target(7, current, goal, path_key, 1.0)
        state = navigator.bot_states[7]
        self.assertIn('pending_since', state)

        navigator._set_fallback_mode(7, None)
        self.assertNotIn('pending_since', state)

    def test_pending_join_replaces_stale_search_after_fallback_moves(self):
        """Cross-cell progress keeps one private job and its fair share."""
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=self._baked_graph(20, 3))
        current = (10.0, 0.0, 24.0)
        goal = (78.0, 0.0, 24.0)
        route_key = ('route', 1, 'blocked-join')
        route_cache_key = navigator._cache_key(route_key, goal)
        navigator.paths[route_cache_key] = (current, goal)
        navigator.path_times[route_cache_key] = 1.0

        shared_key = (('route', 2, 'shared-lane'),
                      navigator.grid.cell_for(goal))
        shared_search = _PendingSearch()
        navigator.searches[shared_key] = shared_search
        navigator.search_times[shared_key] = 1.0
        created = []

        def begin_plan(*unused_args, **unused_kwargs):
            search = _PendingSearch()
            created.append(search)
            return search

        navigator.grid.begin_plan = begin_plan
        navigator.grid.dry_segment_clear = lambda *unused: False
        navigator.grid.segment_clear = lambda *unused: False
        navigator.grid.safe_local_target = lambda point, *unused: (
            point[0] + navigator.grid.cell_size + 0.1,
            point[1], point[2])

        now = 1.0
        selected = navigator.next_target(
            7, current, goal, route_key, now)
        self.assertEqual(current, selected)
        self.assertEqual(2, len(navigator.searches))
        self.assertIn(shared_key, navigator.searches)
        self.assertEqual(1, len(created))

        # The grace expiry supplies one safe local step without replacing the
        # still-current join. The next request starts from another cell and
        # must replace that private job instead of adding a third fair-share
        # participant.
        frame_share = MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME // 2
        now += navigation.PENDING_PROGRESS_SECONDS + 0.01
        before_shared = shared_search.steps
        selected = navigator.next_target(7, current, goal, route_key, now)
        self.assertEqual(frame_share, shared_search.steps - before_shared)
        first_join = created[0]
        current = selected

        for unused in range(5):
            before_shared = shared_search.steps
            now += navigation.PENDING_PROGRESS_SECONDS + 0.01
            selected = navigator.next_target(
                7, current, goal, route_key, now)
            self.assertEqual(frame_share, shared_search.steps - before_shared)
            self.assertEqual(2, len(navigator.searches))
            self.assertIn(shared_key, navigator.searches)
            owned = [
                search for key, search in navigator.searches.items()
                if navigator._path_owner(key[0]) == 7]
            self.assertEqual(1, len(owned))
            current = selected

        self.assertEqual(6, len(created))
        self.assertNotIn(first_join, navigator.searches.values())
        first_join_steps = first_join.steps
        now += navigation.PENDING_PROGRESS_SECONDS + 0.01
        navigator.next_target(7, current, goal, route_key, now)
        self.assertEqual(first_join_steps, first_join.steps)

    def test_cached_private_path_cancels_superseded_pending_search(self):
        """A cached current join still retires another cell's private job."""
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=self._baked_graph(6, 3))
        start = (14.0, 0.0, 24.0)
        goal = (30.0, 0.0, 24.0)
        stale_path_key = ('join', 7, (0, 1), 'route', 1, 'lane')
        current_path_key = ('join', 7, (1, 1), 'route', 1, 'lane')
        stale_key = navigator._cache_key(stale_path_key, goal)
        current_key = navigator._cache_key(current_path_key, goal)
        shared_key = (('route', 2, 'shared-lane'),
                      navigator.grid.cell_for(goal))
        stale_search = _PendingSearch()
        shared_search = _PendingSearch()
        navigator.searches[stale_key] = stale_search
        navigator.searches[shared_key] = shared_search
        navigator.search_times[stale_key] = 1.0
        navigator.search_times[shared_key] = 1.0
        cached_path = (start, goal)
        navigator.paths[current_key] = cached_path
        navigator.path_times[current_key] = 1.0

        selected_key, selected_path = navigator._path(
            current_path_key, start, goal, 2.0, None)

        self.assertEqual(current_key, selected_key)
        self.assertEqual(cached_path, selected_path)
        self.assertNotIn(stale_key, navigator.searches)
        self.assertNotIn(stale_key, navigator.search_times)
        self.assertIs(shared_search, navigator.searches[shared_key])

    def test_cached_private_parent_keeps_pending_child_search(self):
        """One live request may read a route_join while its join is pending."""
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=self._baked_graph(6, 3))
        start = (14.0, 0.0, 24.0)
        goal = (30.0, 0.0, 24.0)
        parent_path_key = ('route_join', 7, 1, 'lane', 1)
        child_path_key = (('join', 7, navigator.grid.cell_for(start)) +
                          parent_path_key)
        parent_key = navigator._cache_key(parent_path_key, goal)
        child_key = navigator._cache_key(child_path_key, goal)
        cached_path = (start, goal)
        child_search = _PendingSearch()
        navigator.paths[parent_key] = cached_path
        navigator.path_times[parent_key] = 1.0
        navigator.searches[child_key] = child_search
        navigator.search_times[child_key] = 1.0

        selected_key, selected_path = navigator._path(
            parent_path_key, start, goal, 2.0, None)

        self.assertEqual(parent_key, selected_key)
        self.assertEqual(cached_path, selected_path)
        self.assertIs(child_search, navigator.searches[child_key])
        self.assertEqual(1.0, navigator.search_times[child_key])

    def test_failed_shallow_search_keeps_reactive_local_recovery(self):
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, 4, 0]
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        navigator._path = lambda *unused: (('search-result',), ())
        navigator.grid.safe_local_target = lambda *unused: None

        selected = navigator.next_target(
            7, current, goal, ('route', 1, 'wet-shortcut'), 1.0)

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
            7, current, math.pi * 0.5 + 0.78))

        retained = navigator.next_target(
            7, current, goal, ('route', 1, 'only-ford'), 1.2)
        self.assertEqual(selected, retained)
        self.assertNotIn(7, navigator.fallback_modes)
        self.assertTrue(navigator.controlled_shallow_step(
            7, current, math.pi * 0.5))

    def _planned_ford(self, bot_id=7):
        """Return a navigator whose only route crosses one shallow cell."""
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, BAKED_SHALLOW_WATER, 0]
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)
        route_key = ('route', 1, 'only-ford')
        navigator.next_target(bot_id, current, goal, route_key, 1.0)
        self.assertEqual((14.0, 0.0, 20.0), navigator.next_target(
            bot_id, current, goal, route_key, 1.1))
        return navigator, current, goal

    def test_fallback_bot_still_follows_the_planner_selected_ford(self):
        for mode, local in (('safe_local', (10.0, 0.0, 22.0)),
                            ('reactive', None)):
            navigator, current, goal = self._planned_ford()
            navigator._path = lambda *unused: (('search-result',), ())
            navigator.grid.safe_local_target = (
                lambda *unused, **kwargs: local)

            navigator.next_target(
                7, current, goal, ('route', 1, 'only-ford'), 1.2)

            self.assertEqual(mode, navigator.fallback_modes[7])
            self.assertTrue(navigator.controlled_shallow_step(
                7, current, math.pi * 0.5))

    def test_first_steering_candidate_offset_may_enter_the_planned_ford(self):
        navigator, current, unused_goal = self._planned_ford()
        offsets = sorted(set(abs(value)
                             for value in LocalDriver._CANDIDATE_OFFSETS))
        bearing = math.pi * 0.5

        self.assertEqual(0.0, offsets[0])
        for sign in (1.0, -1.0):
            self.assertTrue(navigator.controlled_shallow_step(
                7, current, bearing + sign * offsets[1]))
            self.assertFalse(navigator.controlled_shallow_step(
                7, current, bearing + sign * offsets[2]))

    def test_commit_gate_admits_the_lagging_hull_yaw_into_its_own_ford(self):
        """The integrated hull yaw lags the candidate the planner chose.

        ``controlled_shallow_step`` is a cone around the ford bearing sized for
        the driver's candidate fan. Applying it to the post-turn hull yaw
        refused the very rotation the planner asked for, banned that heading
        for five seconds and deleted the decision, so the bot turned away from
        a ford it had legitimately selected and re-selected it on the next
        tactical update.
        """
        navigator, current, unused_goal = self._planned_ford()
        bearing = math.pi * 0.5
        lagging = bearing + 0.48

        self.assertFalse(
            navigator.controlled_shallow_step(7, current, lagging))
        self.assertTrue(
            navigator.controlled_shallow_committed(7, current, lagging))
        self.assertTrue(
            navigator.controlled_shallow_committed(7, current, bearing))

    def test_commit_gate_refuses_a_hull_travelling_away_from_the_ford(self):
        """Commitment is closing on the armed ford, not merely having one."""
        navigator, current, unused_goal = self._planned_ford()
        away = math.pi * 0.5 + math.pi

        self.assertFalse(
            navigator.controlled_shallow_committed(7, current, away))
        self.assertFalse(
            navigator.controlled_shallow_committed(7, current, math.pi))

    def test_commit_gate_needs_an_armed_ford(self):
        """No planner-selected ford means no shallow admission at all."""
        graph = self._baked_graph(3, 1)
        graph['hazards'] = [0, BAKED_SHALLOW_WATER, 0]
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)

        self.assertFalse(navigator.controlled_shallow_committed(
            7, (10.0, 0.0, 20.0), math.pi * 0.5))
        navigator.bot_states[7] = {}
        self.assertFalse(navigator.controlled_shallow_committed(
            7, (10.0, 0.0, 20.0), math.pi * 0.5))

    def test_fallback_drops_a_ford_target_behind_deep_water(self):
        graph = self._baked_graph(3, 1, blocked=((1, 0),))
        graph['hazards'] = [0, 1, 0]
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 20.0)
        goal = (18.0, 0.0, 20.0)
        state = {'controlled_shallow_target': goal}
        navigator.bot_states[7] = state

        navigator._fallback_target(7, current, goal, 1.0, None, state, False)

        self.assertTrue(navigator.grid.segment_has_baked_hazard(
            current, goal, BAKED_FATAL_HAZARDS))
        self.assertNotIn('controlled_shallow_target', state)
        self.assertFalse(navigator.controlled_shallow_step(
            7, current, math.pi * 0.5))

    def test_fallback_drops_a_ford_this_bot_escalated_away_from(self):
        graph = self._baked_graph(3, 1)
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 20.0)
        ford = (18.0, 0.0, 20.0)
        state = {'controlled_shallow_target': ford}
        navigator.bot_states[7] = state
        navigator.bot_states[8] = {'controlled_shallow_target': ford}

        navigator._fallback_target(7, current, ford, 1.0, None, state, False)
        self.assertEqual(ford, state['controlled_shallow_target'])

        edge = navigator.grid._edge_cells_for_segment(current, ford)
        navigator.bot_failed_edges[7] = {edge: (60.0, 240.0)}
        navigator._fallback_target(7, current, ford, 1.0, None, state, False)

        self.assertIsNone(state.get('controlled_shallow_target'))
        # The peer never escalated, so its own ford survives.
        peer = navigator.bot_states[8]
        navigator._fallback_target(8, current, ford, 1.0, None, peer, False)
        self.assertEqual(ford, peer['controlled_shallow_target'])
        self.assertFalse(navigator.grid._failed_edges)

    def test_blocked_step_escalation_reroutes_only_the_reporting_bot(self):
        graph = self._baked_graph(5, 3, blocked=((2, 1),))
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 24.0)
        goal = (26.0, 0.0, 24.0)
        route_key = ('route', 1, 'veto-detour')
        now = 1.0
        for unused_step in range(12):
            now += 0.02
            vetoed = navigator.next_target(11, current, goal, route_key, now)
            peer = navigator.next_target(12, current, goal, route_key, now)

        for offset in (0.0, 0.34, 0.68, 1.02):
            navigator.report_blocked_step(11, current, vetoed, now + offset)
        now += 1.02
        replanned = current
        for unused_step in range(30):
            now += 0.02
            replanned = navigator.next_target(11, current, goal, route_key, now)

        self.assertEqual(vetoed, peer)
        self.assertNotEqual(vetoed, replanned)
        self.assertEqual(peer, navigator.next_target(
            12, current, goal, route_key, now + 0.02))
        self.assertFalse(navigator.grid._failed_edges)
        self.assertNotIn(12, navigator.bot_failed_edges)
        self.assertEqual(
            1, navigator.bot_states[11]['blocked_step_replans'])
        self.assertEqual(0, navigator.bot_states[12].get(
            'blocked_step_replans', 0))

    def test_shore_ford_episode_crosses_instead_of_oscillating(self):
        open_cells = set(((0, 2), (1, 2), (2, 2), (3, 2)))
        for x in range(4, 7):
            for z in range(5):
                open_cells.add((x, z))
        graph = self._baked_graph(7, 5, blocked=tuple(
            (x, z) for z in range(5) for x in range(7)
            if (x, z) not in open_cells))
        graph['hazards'][2 * 7 + 3] = BAKED_SHALLOW_WATER
        navigator = TerrainNavigator(lambda *unused: None, baked_graph=graph)
        grid = navigator.grid
        driver = LocalDriver()
        goal = (34.0, 0.0, 28.0)
        route_key = ('route', 1, 'shore-ford')
        position = [10.0, 0.0, 28.0]
        yaw = [0.0]
        planned = navigator._path

        def shore_search(path_key, start, target, now, avoid_points):
            # A search near the shore fails after A* has selected the ford.
            if position[0] >= 17.0:
                return (('shore-search-failed',), ())
            return planned(path_key, start, target, now, avoid_points)

        def direction_clear(sample_yaw):
            # Same one-cell corridor rule as the runtime planner gate.
            end = (position[0] + math.sin(sample_yaw) * grid.cell_size,
                   position[1],
                   position[2] + math.cos(sample_yaw) * grid.cell_size)
            mask = BAKED_FATAL_HAZARDS
            if not navigator.controlled_shallow_step(
                    9, tuple(position), sample_yaw):
                mask |= BAKED_SHALLOW_WATER
            return (not grid.segment_has_baked_hazard(
                        tuple(position), end, mask) and
                    grid.segment_clear(tuple(position), end))

        navigator._path = shore_search
        now = 1.0
        near_bank_fallbacks = set()
        for unused_step in range(200):
            target = navigator.next_target(
                9, tuple(position), goal, route_key, now)
            mode = navigator.fallback_modes.get(9)
            if position[0] < 22.0 and mode in ('safe_local', 'reactive'):
                near_bank_fallbacks.add(mode)
            command = driver.drive(
                9, tuple(position), yaw[0], 0.0, 0.1, target, (),
                direction_clear)
            yaw[0] = float(command['target_yaw'])
            throttle = float(command['throttle'])
            travel = yaw[0] if throttle >= 0.0 else yaw[0] + math.pi
            if abs(throttle) > 0.0 and direction_clear(travel):
                position[0] += math.sin(travel) * 4.0 * abs(throttle) * 0.1
                position[2] += math.cos(travel) * 4.0 * abs(throttle) * 0.1
            now += 0.1

        self.assertTrue(near_bank_fallbacks)
        self.assertGreater(position[0], 30.0)
        self.assertFalse(grid.point_has_baked_hazard(
            tuple(position), BAKED_FATAL_HAZARDS | BAKED_SHALLOW_WATER))

    def test_prebaked_segment_rejects_destination_beyond_map_bounds(self):
        graph = self._baked_graph(3, 1)
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)

        self.assertTrue(grid.segment_clear(
            (14.0, 0.0, 20.0), (18.0, 0.0, 20.0)))
        self.assertFalse(grid.segment_clear(
            (14.0, 0.0, 20.0), (22.1, 0.0, 20.0)))

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

    @staticmethod
    def _pending_search_navigator(count):
        navigator = TerrainNavigator(lambda *unused: 0.0)
        searches = [_PendingSearch() for unused in range(count)]
        navigator.searches = dict(
            (('job', index), search)
            for index, search in enumerate(searches))
        return navigator, searches

    def test_astar_work_uses_elapsed_credit_with_a_hard_frame_cap(self):
        navigator, searches = self._pending_search_navigator(4)

        navigator.begin_frame(0.05)
        navigator.tick(1.0)
        navigator.end_frame()

        earned = int(0.05 * SEARCH_EXPANSIONS_PER_SECOND)
        self.assertEqual(earned, sum(search.steps for search in searches))
        self.assertLessEqual(
            max(search.steps for search in searches) -
            min(search.steps for search in searches), 1)

        navigator.begin_frame(1.0)
        navigator.tick(2.0)
        navigator.end_frame()

        self.assertEqual(
            earned + MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME,
            sum(search.steps for search in searches))
        self.assertLessEqual(
            max(search.steps for search in searches) -
            min(search.steps for search in searches), 1)

    def _drive_search_frames(self, frame_interval, seconds, job_count=8):
        """Return total and peak-frame expansions for one simulated frame rate."""
        navigator, searches = self._pending_search_navigator(job_count)
        frames = int(round(float(seconds) / float(frame_interval)))
        now = 0.0
        peak = 0
        for unused in range(frames):
            before = sum(search.steps for search in searches)
            now += frame_interval
            navigator.begin_frame(frame_interval)
            navigator.tick(now)
            navigator.end_frame()
            peak = max(peak, sum(search.steps for search in searches) - before)
        spread = (max(search.steps for search in searches) -
                  min(search.steps for search in searches))
        return sum(search.steps for search in searches), peak, spread

    def test_astar_throughput_is_independent_of_render_frame_rate(self):
        seconds = 2.4
        results = dict(
            (fps, self._drive_search_frames(1.0 / fps, seconds))
            for fps in (5, 10, 30, 60))
        expected = int(seconds * SEARCH_EXPANSIONS_PER_SECOND)

        for fps, (total, peak, spread) in sorted(results.items()):
            self.assertEqual(expected, total, 'throughput differs at %d fps' % fps)
            self.assertLessEqual(
                peak, MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME)
            self.assertLessEqual(spread, 1)

        self.assertEqual(
            MAX_SEARCH_EXPANSIONS_PER_FRAME, results[10][1])
        self.assertEqual(
            MAX_SEARCH_EXPANSIONS_PER_FRAME * 2, results[5][1])
        self.assertEqual(
            int(SEARCH_EXPANSIONS_PER_SECOND / 60.0), results[60][1])

    def test_astar_catch_up_after_a_stall_stays_bounded(self):
        navigator, searches = self._pending_search_navigator(8)

        navigator.begin_frame(5.0)
        navigator.tick(5.0)
        navigator.end_frame()

        self.assertEqual(
            MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME,
            sum(search.steps for search in searches))
        self.assertEqual(0.0, navigator.search_credit)

        navigator.begin_frame(0.1)
        navigator.tick(5.1)
        navigator.end_frame()

        self.assertEqual(
            MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME +
            MAX_SEARCH_EXPANSIONS_PER_FRAME,
            sum(search.steps for search in searches))

    def test_astar_frame_share_starves_no_pending_search(self):
        navigator, searches = self._pending_search_navigator(29)

        now = 0.0
        for unused in range(20):
            now += 0.2
            navigator.begin_frame(0.2)
            navigator.tick(now)
            navigator.end_frame()

        self.assertEqual(
            int(4.0 * SEARCH_EXPANSIONS_PER_SECOND),
            sum(search.steps for search in searches))
        self.assertLessEqual(
            max(search.steps for search in searches) -
            min(search.steps for search in searches), 1)
        self.assertTrue(all(search.steps > 0 for search in searches))

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

    def test_repeated_blocked_step_replans_only_the_blocked_bot(self):
        graph = self._baked_graph(5, 3)
        navigator = TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        current = (10.0, 0.0, 24.0)
        goal = (26.0, 0.0, 24.0)
        route_key = ('route', 1, 'contact-detour')
        navigator.next_target(11, current, goal, route_key, 1.0)

        escalated = [navigator.report_blocked_step(
            11, current, goal, now) for now in (1.0, 1.34, 1.68, 2.02)]

        self.assertEqual([False, False, False, True], escalated)
        self.assertFalse(navigator.grid._failed_edges)
        self.assertTrue(navigator.bot_failed_edges[11])
        self.assertNotIn(12, navigator.bot_failed_edges)
        navigator.next_target(11, current, goal, route_key, 2.03)
        navigator.next_target(11, current, goal, route_key, 2.04)
        active_key = navigator.bot_states[11]['path_key']
        self.assertEqual('recovery', active_key[0][0])
        self.assertEqual(1, navigator.bot_states[11]['blocked_step_replans'])

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
        self.assertTrue(order['route_join'])

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

    def test_current_hull_yaw_detects_front_overlap_during_route_turn(self):
        driver = LocalDriver()
        position = (0.0, 0.0, 0.0)
        desired_yaw = math.pi * 0.5
        neighbour = {
            'position': (0.0, 0.0, 6.0),
            'yaw': 0.0,
            'half_length': 3.5,
            'half_width': 1.7,
        }
        self.assertTrue(driver._obb_overlap(
            position, 0.0, 3.7, 1.9,
            neighbour['position'], 0.0, 3.7, 1.9))
        self.assertFalse(driver._obb_overlap(
            position, desired_yaw, 3.7, 1.9,
            neighbour['position'], 0.0, 3.7, 1.9))

        order = driver.drive(
            134, position, 0.0, 0.0, 0.1,
            (50.0, 0.0, 0.0), (neighbour,),
            lambda unused_yaw: True,
            half_length=3.5, half_width=1.7)

        self.assertEqual('avoid', order['recovery_mode'])
        self.assertGreater(
            abs(order['target_yaw'] - desired_yaw), 1.0)

    def test_current_hull_yaw_ignores_side_gap_during_route_turn(self):
        driver = LocalDriver()
        position = (0.0, 0.0, 0.0)
        desired_yaw = math.pi * 0.5
        neighbour = {
            'position': (5.0, 0.0, 0.0),
            'yaw': 0.0,
            'half_length': 3.5,
            'half_width': 1.7,
        }
        self.assertFalse(driver._obb_overlap(
            position, 0.0, 3.7, 1.9,
            neighbour['position'], 0.0, 3.7, 1.9))
        self.assertTrue(driver._obb_overlap(
            position, desired_yaw, 3.7, 1.9,
            neighbour['position'], 0.0, 3.7, 1.9))

        order = driver.drive(
            135, position, 0.0, 0.0, 0.1,
            (50.0, 0.0, 0.0), (neighbour,),
            lambda unused_yaw: True,
            half_length=3.5, half_width=1.7)

        self.assertEqual('drive', order['recovery_mode'])
        self.assertAlmostEqual(desired_yaw, order['target_yaw'])

    def test_failed_yaw_cache_uses_circular_buckets(self):
        driver = LocalDriver()
        state = driver._state(136, (0.0, 0.0, 0.0))
        turn = math.pi * 2.0
        for yaw in (-math.pi, -2.70, -0.51, 0.13, 1.73,
                    math.pi - 0.11):
            expected = driver._yaw_key(yaw)
            for turns in range(-4, 5):
                self.assertEqual(
                    expected, driver._yaw_key(yaw + turns * turn))
        self.assertEqual(
            driver._yaw_key(math.pi), driver._yaw_key(-math.pi))

        # The four common hull headings are bucket centres, not representation
        # seams where tiny measurement noise would select another penalty.
        for yaw in (-math.pi, -math.pi * 0.5, 0.0, math.pi * 0.5):
            expected = driver._yaw_key(yaw)
            self.assertEqual(expected, driver._yaw_key(yaw - 0.04))
            self.assertEqual(expected, driver._yaw_key(yaw + 0.04))

        failed_yaw = math.pi - 0.04
        equivalents = (
            failed_yaw - turn * 3.0,
            failed_yaw + turn * 4.0,
        )
        driver.remember_failure(136, failed_yaw, ttl=5.0)
        expected_penalty = driver._failure_penalty(state, failed_yaw)
        self.assertGreater(expected_penalty, 0.0)
        for yaw in equivalents:
            self.assertEqual(driver._yaw_key(failed_yaw),
                             driver._yaw_key(yaw))
            self.assertAlmostEqual(
                expected_penalty, driver._failure_penalty(state, yaw))

        # These are distinct numeric angles on opposite sides of +/-pi but
        # adjacent physical directions inside the same circular bucket.
        opposite_seam_side = -math.pi + 0.04
        self.assertEqual(driver._yaw_key(failed_yaw),
                         driver._yaw_key(opposite_seam_side))
        self.assertAlmostEqual(
            expected_penalty,
            driver._failure_penalty(state, opposite_seam_side))

    def test_slow_callback_advances_the_complete_planner_interval(self):
        driver = LocalDriver()

        driver.drive(
            3, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)

        state = driver.states[3]
        self.assertAlmostEqual(1.0, state['last_step'])
        self.assertAlmostEqual(1.0, state['clock'])

    def test_large_route_turn_pivots_before_driving_and_never_reverses(self):
        # A stable target with monotonically shrinking heading error receives a
        # finite pivot lease without treating arbitrary rotation as translation.
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

    def test_alternating_stationary_turns_reach_stuck_recovery(self):
        driver = LocalDriver()
        yaw = 0.0
        recovery = None
        for tick in range(300):
            target_yaw = 1.15 if tick % 2 == 0 else -1.15
            target = (math.sin(target_yaw) * 50.0, 0.0,
                      math.cos(target_yaw) * 50.0)
            order = driver.drive(
                70, (0.0, 0.0, 0.0), yaw, 0.0, 1.0 / 30.0,
                target, (), lambda unused_yaw: True)
            if order['recovery_mode'] in ('reverse_turn', 'pivot_recovery'):
                recovery = order
                break
            yaw += order['turn'] * 0.66 * (1.0 / 30.0)

        self.assertIsNotNone(recovery)
        self.assertGreater(driver.states[70]['stuck_time'], 0.0)

    def test_terminal_target_coasts_inside_copied_stopping_distance(self):
        driver = LocalDriver()
        terminal = driver.drive(
            71, (0.0, 0.0, 0.0), 0.0, 14.0, 0.15,
            (0.0, 0.0, 8.0), (), lambda unused_yaw: True,
            stopping_distance=6.0, stop_at_target=True,
            decision_horizon=0.15)
        corridor = driver.drive(
            72, (0.0, 0.0, 0.0), 0.0, 14.0, 0.15,
            (0.0, 0.0, 8.0), (), lambda unused_yaw: True,
            stopping_distance=6.0, stop_at_target=False,
            decision_horizon=0.15)

        self.assertEqual(0.0, terminal['throttle'])
        self.assertEqual(1.0, corridor['throttle'])

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

    def test_traffic_lease_uses_explicit_time_not_decision_steps(self):
        """An external lease producer must supply its physical interval.

        LocalDriver.drive only runs on a decision callback, so last_step holds
        the planner's decision interval. A physical contact producer cannot
        default to that unrelated duration.
        """
        driver = LocalDriver()
        driver.drive(
            41, (0.0, 0.0, 0.0), 0.0, 0.0, 0.15,
            (0.0, 0.0, 50.0), (), lambda unused_yaw: True)
        state = driver.states[41]
        self.assertAlmostEqual(0.15, state['last_step'])

        for unused in range(9):
            driver.wait_for_traffic(41, 1.0 / 60.0)

        self.assertAlmostEqual(9.0 / 60.0, state['traffic_wait_time'])
        self.assertLess(state['traffic_wait_time'],
                        TRAFFIC_WAIT_LEASE_SECONDS)

    def test_wedged_hull_never_reverses_into_the_tank_behind_it(self):
        """direction_clear answers for terrain, not for the queue behind.

        In a spawn line-up every tank reaches the stuck threshold at about the
        same time, so an unchecked reverse recovery drives each hull into the
        one behind it.
        """
        driver = LocalDriver()
        behind = ({'position': (0.0, 0.0, -7.0), 'yaw': 0.0,
                   'half_length': 3.5, 'half_width': 1.7},)
        modes = set()
        for unused in range(150):
            order = driver.drive(
                9, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0 / 30.0,
                (0.0, 0.0, 50.0), behind, lambda unused_yaw: True)
            modes.add(order['recovery_mode'])

        self.assertIn('pivot_recovery', modes)
        self.assertNotIn('reverse_turn', modes)

    def test_reverse_guard_checks_the_complete_reachable_hull_sweep(self):
        """A blocker before the sampled endpoint is still in the sweep."""
        driver = LocalDriver()
        position = (0.0, 0.0, 0.0)
        half_length = 2.0
        half_width = 1.0
        transverse = ({
            'position': (0.0, 0.0, -0.5),
            'yaw': math.pi / 2.0,
            'half_length': 2.0,
            'half_width': 0.5,
        },)
        endpoint = (0.0, 0.0, -half_length * 1.6)

        self.assertFalse(driver._obb_overlap(
            endpoint, 0.0, half_length, half_width,
            transverse[0]['position'], transverse[0]['yaw'],
            transverse[0]['half_length'], transverse[0]['half_width']))
        self.assertTrue(driver._reverse_blocked_by_vehicle(
            position, 0.0, transverse, half_length, half_width))

    def test_wedged_hull_still_reverses_when_the_space_behind_is_free(self):
        """The vehicle check must not disable reverse recovery generally."""
        driver = LocalDriver()
        far = ({'position': (0.0, 0.0, -40.0), 'yaw': 0.0,
                'half_length': 3.5, 'half_width': 1.7},)
        modes = set()
        for unused in range(150):
            order = driver.drive(
                10, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0 / 30.0,
                (0.0, 0.0, 50.0), far, lambda unused_yaw: True)
            modes.add(order['recovery_mode'])

        self.assertIn('reverse_turn', modes)

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
