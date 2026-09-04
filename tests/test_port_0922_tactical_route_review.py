import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
NAVGRAPHS = ROOT / 'navgraphs'


def load_tool(name):
    path = ROOT / 'tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location('route_review_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_tool('render_tactical_routes')
extractor = load_tool('extract_tactical_annotations')


def load_graph(map_name):
    return json.loads(
        (NAVGRAPHS / (map_name + '.json')).read_text(encoding='utf-8'))


def route_points(graph, route_id, team):
    matches = [route for route in graph['routes'][str(team)]
               if route['id'] == route_id]
    if len(matches) != 1:
        raise AssertionError('%s team %s has %d routes named %s' % (
            graph['map'], team, len(matches), route_id))
    return tuple((float(point[0]), float(point[1]))
                 for point in matches[0]['waypoints'])


def median(values):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise AssertionError('median needs at least one value')
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) * 0.5


def heading_changes(points):
    changes = []
    for first, middle, last in zip(points, points[1:], points[2:]):
        incoming = (middle[0] - first[0], middle[1] - first[1])
        outgoing = (last[0] - middle[0], last[1] - middle[1])
        scale = math.hypot(*incoming) * math.hypot(*outgoing)
        if scale <= 0.000001:
            continue
        cosine = ((incoming[0] * outgoing[0] +
                   incoming[1] * outgoing[1]) / scale)
        changes.append(math.degrees(math.acos(
            max(-1.0, min(1.0, cosine)))))
    return changes


def backward_progress(points):
    direction = (points[-1][0] - points[0][0],
                 points[-1][1] - points[0][1])
    length = math.hypot(*direction)
    if length <= 0.000001:
        raise AssertionError('route endpoints must be distinct')
    unit = (direction[0] / length, direction[1] / length)
    projected = [point[0] * unit[0] + point[1] * unit[1]
                 for point in points]
    return tuple(current - previous
                 for previous, current in zip(projected, projected[1:]))


def point_to_polyline_distance(point, points):
    best = float('inf')
    for first, last in zip(points, points[1:]):
        dx = last[0] - first[0]
        dz = last[1] - first[1]
        length_squared = dx * dx + dz * dz
        if length_squared <= 0.000001:
            amount = 0.0
        else:
            amount = (((point[0] - first[0]) * dx +
                       (point[1] - first[1]) * dz) / length_squared)
            amount = max(0.0, min(1.0, amount))
        nearest = (first[0] + dx * amount, first[1] + dz * amount)
        best = min(best, math.hypot(
            point[0] - nearest[0], point[1] - nearest[1]))
    return best


def sample_graph():
    return {
        'game_version': '0.9.22.0.1-cn-1513',
        'map': '99_contract',
        'bounds': [0.0, 0.0, 100.0, 100.0],
        # This deliberately disagrees with the two #1513 contracts.  Review
        # tools must never recover the old ambiguous meaning from this field.
        'bases': [[0.0, 0.0], [100.0, 100.0]],
        'spawn_anchors': [[10.0, 10.0], [90.0, 90.0]],
        'objective_bases': [[30.0, 30.0], [70.0, 70.0]],
        'spawn_formations': {'1': [], '2': []},
        'routes': {
            '1': [
                {'id': 'through', 'waypoints': [
                    [10.0, 10.0, False], [90.0, 90.0, False]]},
                {'id': 'local', 'waypoints': [
                    [10.0, 10.0, False], [20.0, 20.0, True]]},
            ],
            '2': [
                {'id': 'through', 'waypoints': [
                    [90.0, 90.0, False], [10.0, 10.0, False]]},
                {'id': 'local', 'waypoints': [
                    [90.0, 90.0, False], [80.0, 80.0, True]]},
            ],
        },
    }


class TacticalRouteReviewTest(unittest.TestCase):

    def test_pinned_graph_inventory_and_review_route_counts(self):
        paths = sorted(
            (path for path in NAVGRAPHS.glob('*.json')
             if path.name != 'manifest.json'),
            key=renderer._map_sort_key)
        self.assertEqual(41, len(paths))
        self.assertEqual('01_karelia', paths[0].stem)
        names = [path.stem for path in paths]
        self.assertLess(names.index('95_lost_city'),
                        names.index('100_thepit'))
        self.assertEqual('114_czech', names[-1])
        route_records = 0
        unique_routes = 0
        for path in paths:
            graph = json.loads(path.read_text(encoding='utf-8'))
            anchors = renderer._point_pair(graph, 'spawn_anchors')
            objectives = renderer._point_pair(graph, 'objective_bases')
            self.assertEqual(2, len(anchors))
            self.assertEqual(2, len(objectives))
            team_1 = graph['routes']['1']
            team_2 = graph['routes']['2']
            if path.stem == '04_himmelsdorf':
                expected = 4
            elif path.stem == '06_ensk':
                expected = 2
            else:
                expected = 3
            self.assertEqual(expected, len(team_1), path.stem)
            self.assertEqual(expected, len(team_2), path.stem)
            self.assertEqual([route['id'] for route in team_1],
                             [route['id'] for route in team_2])
            team_2_by_id = {route['id']: route for route in team_2}
            for route in team_1:
                if (route['id'] == 'rear_guard' or
                        route.get('terminal_hold', False)):
                    continue
                reverse = team_2_by_id[route['id']]
                self.assertEqual(
                    route['waypoints'],
                    list(reversed(reverse['waypoints'])),
                    '%s:%s is not one reversible corridor' %
                    (path.stem, route['id']))
            route_records += len(team_1) + len(team_2)
            unique_routes += len(team_1)
        self.assertEqual(246, route_records)
        self.assertEqual(123, unique_routes)

    def test_decoded_team_starts_select_all_fifteen_corrected_route_gates(self):
        # These are the eight geometry-changing graphs from the full 41-map
        # rebake. The coordinates are validated graph cells nearest the hard
        # gates chosen against exact #1513 team starts.
        contracts = {
            '11_murovanka': {
                'west_woods': (-390.0, -50.0),
                'central_field': (18.0, -254.0),
                'east_village': (322.0, -6.0),
            },
            '13_erlenberg': {
                'north_bridge': (434.0, 138.0),
                'south_bridge': (-422.0, -138.0),
            },
            '22_slough': {
                'west_ridge': (-158.0, -294.0),
                'middle_low': (-42.0, 226.0),
                'east_ridge': (246.0, 326.0),
            },
            '63_tundra': {
                'waterfall': (-178.0, 42.0),
                'plateau': (346.0, 90.0),
                'village': (-370.0, -70.0),
            },
            '73_asia_korea': {'temple': (94.0, -18.0)},
            '83_kharkiv': {'factory': (-2.0, 10.0)},
            '84_winter': {'ice_road': (150.0, 318.0)},
            '92_stalingrad': {'city': (-378.0, -102.0)},
        }
        self.assertEqual(8, len(contracts))
        self.assertEqual(15, sum(len(routes) for routes in contracts.values()))
        for map_name, routes in sorted(contracts.items()):
            graph = load_graph(map_name)
            for team in ('1', '2'):
                by_id = {route['id']: route
                         for route in graph['routes'][team]}
                for route_id, expected_gate in sorted(routes.items()):
                    with self.subTest(
                            map=map_name, route=route_id, team=team):
                        held = [tuple(point[:2])
                                for point in by_id[route_id]['waypoints']
                                if bool(point[2])]
                        self.assertEqual([expected_gate], held)

    def test_rebake_syncs_two_preexisting_metadata_only_drifts(self):
        karelia = {
            route['id']: route
            for route in load_graph('01_karelia')['routes']['1']
        }
        self.assertEqual(
            {'west_ridge': 5, 'middle_road': 4, 'east_shelf': 6},
            {route_id: route['capacity']
             for route_id, route in karelia.items()})
        self.assertEqual(
            {'artillery': 0.0, 'brawler': 0.35, 'flanker': 0.82,
             'scout': 0.24, 'sniper': 0.72, 'support': 0.78},
            karelia['west_ridge']['role_weights'])
        self.assertEqual(
            {'artillery': 0.0, 'brawler': 0.02, 'flanker': 0.5,
             'scout': 1.0, 'sniper': 0.22, 'support': 0.28},
            karelia['middle_road']['role_weights'])
        self.assertEqual(
            {'artillery': 0.0, 'brawler': 1.0, 'flanker': 0.62,
             'scout': 0.12, 'sniper': 0.25, 'support': 0.72},
            karelia['east_shelf']['role_weights'])

        malinovka = {
            route['id']: route['capacity']
            for route in load_graph('02_malinovka')['routes']['1']
        }
        self.assertEqual(
            {'west_lake_road': 3, 'central_field': 5,
             'east_hill_loop': 6},
            malinovka)

    def test_renderer_uses_exact_spawn_and_objective_contracts_at_1200(self):
        graph = sample_graph()
        spawn_calls = []
        base_calls = []

        def capture_spawn(unused_draw, point, unused_colour, label,
                          unused_font, unused_width):
            spawn_calls.append((point, label))

        def capture_base(unused_draw, point, unused_colour, label,
                         unused_font, unused_width):
            base_calls.append((point, label))

        with mock.patch.object(renderer, '_spawn_marker', capture_spawn), \
                mock.patch.object(renderer, '_base_marker', capture_base):
            image = renderer._render(
                graph, Image.new('RGB', (64, 64), (0, 0, 0)), 1200)
        self.assertEqual((1200, 1284), image.size)
        self.assertEqual(['SPAWN 1', 'SPAWN 2'],
                         [item[1] for item in spawn_calls])
        self.assertEqual(['BASE 1', 'BASE 2'],
                         [item[1] for item in base_calls])
        extent = 1199.0
        self.assertAlmostEqual(0.10 * extent, spawn_calls[0][0][0])
        self.assertAlmostEqual(0.30 * extent, base_calls[0][0][0])
        self.assertNotEqual(0.0, spawn_calls[0][0][0])

    def test_extractor_accepts_1200_geometry_and_uses_spawn_anchors(self):
        graph = sample_graph()
        anchors = extractor._anchor_pixels(graph, 1200)
        self.assertAlmostEqual(119.9, anchors[0][0])
        self.assertAlmostEqual(1079.1, anchors[1][0])
        existing = extractor._existing_routes(graph, 1200)
        through, manual = extractor._split_through_routes(existing, anchors)
        self.assertEqual(['through'], [item[0] for item in through])
        self.assertEqual(['local'], manual)
        world = extractor._world_points(anchors, graph, 1200)
        self.assertEqual([[10.0, 10.0], [90.0, 90.0]], world)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / '99_contract.png'
            reviewed = root / '99_contract-reviewed.png'
            graph_path = root / '99_contract.json'
            image = Image.new('RGB', (1200, 1284), (24, 26, 29))
            image.save(original)
            image.save(reviewed)
            graph_path.write_text(json.dumps(graph), encoding='utf-8')
            result = extractor.extract_map(
                reviewed, original, graph_path, root)
            self.assertEqual(0, result['red_pixels_downsampled'])
            self.assertEqual(['local'], result['manual_review_routes'])
            self.assertEqual([], result['routes'])

            annotated = image.copy()
            draw = ImageDraw.Draw(annotated)
            draw.line([(anchors[0][0], anchors[0][1] + extractor.HEADER),
                       (600.0, 600.0 + extractor.HEADER),
                       (anchors[1][0], anchors[1][1] + extractor.HEADER)],
                      fill=(255, 0, 0), width=16, joint='curve')
            annotated.save(reviewed)
            result = extractor.extract_map(
                reviewed, original, graph_path, root)
        self.assertEqual(['through'],
                         [route['id'] for route in result['routes']])
        self.assertEqual([10.0, 10.0], result['routes'][0]['world'][0])
        self.assertEqual([90.0, 90.0], result['routes'][0]['world'][-1])

    def test_round_two_himmelsdorf_lanes_stay_separate_and_smooth(self):
        graph = load_graph('04_himmelsdorf')
        for team in ('1', '2'):
            with self.subTest(team=team):
                banana = route_points(graph, 'banana', team)
                hill = route_points(graph, 'hill', team)
                banana_core = [point for point in banana
                               if -200.0 <= point[1] <= 260.0]
                hill_core = [point for point in hill
                             if -200.0 <= point[1] <= 260.0]
                self.assertGreaterEqual(len(banana_core), 5)
                self.assertGreaterEqual(len(hill_core), 4)
                self.assertGreaterEqual(
                    sum(35.0 <= x <= 220.0 for x, unused_z in banana_core),
                    int(math.ceil(len(banana_core) * 0.75)))
                self.assertGreater(
                    median(x for x, unused_z in hill_core) -
                    median(x for x, unused_z in banana_core),
                    150.0)
                # More than one near-right-angle turn is the old building
                # zig-zag, not the reviewed road below the cliff.
                self.assertLessEqual(
                    sum(change > 100.0 for change in
                        heading_changes(banana)),
                    1)

    def test_round_four_ensk_has_only_two_balanced_reviewed_corridors(self):
        graph = load_graph('06_ensk')
        for team in ('1', '2'):
            with self.subTest(team=team):
                routes = graph['routes'][team]
                self.assertEqual(
                    ['west_city', 'east_field'],
                    [route['id'] for route in routes])
                self.assertEqual([7, 7],
                                 [route['capacity'] for route in routes])

    def test_round_two_lakeville_lake_road_does_not_fall_into_west_valley(self):
        graph = load_graph('07_lakeville')
        for team in ('1', '2'):
            with self.subTest(team=team):
                lake = route_points(graph, 'lake_road', team)
                valley = route_points(graph, 'west_valley', team)
                lake_core = [point for point in lake
                             if -230.0 <= point[1] <= 240.0]
                valley_core = [point for point in valley
                               if -230.0 <= point[1] <= 240.0]
                self.assertGreaterEqual(len(lake_core), 6)
                self.assertGreaterEqual(len(valley_core), 6)
                self.assertGreaterEqual(
                    sum(-145.0 <= x <= -70.0
                        for x, unused_z in lake_core),
                    int(math.ceil(len(lake_core) * 0.75)))
                self.assertGreater(
                    median(x for x, unused_z in lake_core) -
                    median(x for x, unused_z in valley_core),
                    150.0)

    def test_round_two_siegfried_fortification_route_stays_in_city(self):
        graph = load_graph('14_siegfried_line')
        for team in ('1', '2'):
            with self.subTest(team=team):
                route = route_points(graph, 'fortification_line', team)
                city = [point for point in route
                        if -300.0 <= point[1] <= 300.0]
                self.assertGreaterEqual(len(city), 7)
                self.assertGreater(median(x for x, unused_z in city), 210.0)
                self.assertGreaterEqual(
                    sum(x >= 180.0 for x, unused_z in city),
                    int(math.ceil(len(city) * 0.75)))

    def test_round_two_munchen_west_route_uses_left_underpass(self):
        graph = load_graph('17_munchen')
        for team in ('1', '2'):
            with self.subTest(team=team):
                route = route_points(graph, 'west_streets', team)
                self.assertLess(
                    point_to_polyline_distance((-214.0, -70.0), route),
                    18.0)
                self.assertTrue(any(
                    -202.0 <= x <= -186.0 and 74.0 <= z <= 106.0
                    for x, z in route))
                self.assertLess(max(heading_changes(route)), 135.0)

    def test_round_two_monastery_city_lane_is_direct(self):
        graph = load_graph('19_monastery')
        for team in ('1', '2'):
            with self.subTest(team=team):
                route = route_points(graph, 'monastery_lane', team)
                city = [point for point in route
                        if -260.0 <= point[1] <= 280.0]
                self.assertGreaterEqual(len(city), 7)
                self.assertGreater(median(x for x, unused_z in city), -20.0)
                self.assertLessEqual(
                    sum(x < -80.0 for x, unused_z in city),
                    1)

    def test_round_two_canada_central_road_has_no_westward_hook(self):
        graph = load_graph('47_canada_a')
        for team in ('1', '2'):
            with self.subTest(team=team):
                route = route_points(graph, 'central_road', team)
                upper_road = [point for point in route
                              if 80.0 <= point[1] <= 240.0]
                self.assertGreaterEqual(len(upper_road), 2)
                self.assertGreaterEqual(
                    min(x for x, unused_z in upper_road), -80.0)
                self.assertLess(max(heading_changes(route)), 80.0)

    def test_round_two_great_wall_routes_keep_three_distinct_roles(self):
        graph = load_graph('59_asia_great_wall')
        for team in ('1', '2'):
            with self.subTest(team=team):
                wall = route_points(graph, 'wall_pass', team)
                valley = route_points(graph, 'valley', team)
                ridge = route_points(graph, 'ridge', team)

                # The central line and upper mountain line approach the real
                # eastern gatehouse from different sides, while the third
                # route remains the lower western ridge.
                self.assertLess(
                    point_to_polyline_distance((404.0, -185.0), wall),
                    24.0)
                self.assertLess(
                    point_to_polyline_distance((404.0, -150.0), valley),
                    28.0)
                self.assertTrue(any(
                    x >= 440.0 and z >= 100.0 for x, z in valley))
                # Round four deliberately rounds this southwest corner rather
                # than forcing the old sharp (-430, -300) gate.
                self.assertLess(
                    point_to_polyline_distance((-290.0, -414.0), ridge),
                    14.0)

                wall_upper = [x for x, z in wall
                              if 250.0 <= z <= 380.0]
                valley_upper = [x for x, z in valley
                                if 250.0 <= z <= 380.0]
                self.assertTrue(wall_upper)
                self.assertTrue(valley_upper)
                self.assertGreater(
                    median(valley_upper) - median(wall_upper),
                    300.0)

    def test_round_two_korea_hills_has_fewer_major_bends(self):
        graph = load_graph('73_asia_korea')
        for team in ('1', '2'):
            with self.subTest(team=team):
                changes = heading_changes(
                    route_points(graph, 'hills', team))
                self.assertLess(sum(changes), 540.0)
                self.assertLessEqual(
                    sum(change > 60.0 for change in changes), 5)

    def test_round_four_reviewed_gates_remove_marked_route_hooks(self):
        contracts = (
            ('29_el_hallouf', 'south_valley', (-86.0, -198.0), 90.0),
            ('31_airfield', 'south_towns', (-70.0, -260.0), 95.0),
            ('37_caucasus', 'central_basin', (70.0, 60.0), 80.0),
            ('73_asia_korea', 'river', (-300.0, 250.0), 60.0),
            ('84_winter', 'town', (20.0, -305.0), 60.0),
            ('86_himmelsdorf_winter', 'rail', (-160.0, 230.0), 60.0),
            ('92_stalingrad', 'railway', (250.0, 0.0), 55.0),
            ('92_stalingrad', 'embankment', (48.0, -130.0), 55.0),
            ('114_czech', 'town', (-312.0, -90.0), 60.0),
            ('114_czech', 'valley', (62.0, 160.0), 60.0),
        )
        for map_name, route_id, gate, maximum_turn in contracts:
            graph = load_graph(map_name)
            for team in ('1', '2'):
                with self.subTest(map=map_name, route=route_id, team=team):
                    route = route_points(graph, route_id, team)
                    self.assertLess(
                        point_to_polyline_distance(gate, route), 12.0)
                    self.assertLess(max(heading_changes(route)), maximum_turn)

    def test_round_four_north_america_uses_the_reviewed_fords_and_underpass(self):
        graph = load_graph('45_north_america')
        for team in ('1', '2'):
            with self.subTest(team=team):
                north = route_points(graph, 'north_road', team)
                river = route_points(graph, 'river_crossing', team)
                self.assertLess(
                    point_to_polyline_distance((-146.0, 374.0), north),
                    12.0)
                self.assertLess(
                    point_to_polyline_distance((-30.0, 0.0), river),
                    12.0)
                # The sampled review polyline must cross the installed ford;
                # a soft source point need not survive route simplification.
                self.assertLess(
                    point_to_polyline_distance((-150.0, -238.0), river),
                    12.0)
                self.assertLess(max(heading_changes(north)), 90.0)
                self.assertLess(max(heading_changes(river)), 90.0)

    def test_round_four_great_wall_uses_mountain_gap_and_wider_ridge_corner(self):
        graph = load_graph('59_asia_great_wall')
        for team in ('1', '2'):
            with self.subTest(team=team):
                wall = route_points(graph, 'wall_pass', team)
                ridge = route_points(graph, 'ridge', team)
                self.assertLess(
                    point_to_polyline_distance((-180.0, 360.0), wall),
                    14.0)
                self.assertLess(
                    point_to_polyline_distance((-290.0, -414.0), ridge),
                    14.0)
                self.assertLess(
                    point_to_polyline_distance((-428.0, -274.0), ridge),
                    8.0)
                self.assertLess(max(heading_changes(wall)), 80.0)
                self.assertLess(max(heading_changes(ridge)), 55.0)

    def test_missing_explicit_contract_is_rejected(self):
        graph = sample_graph()
        del graph['spawn_anchors']
        with self.assertRaisesRegex(ValueError, 'spawn_anchors'):
            extractor._anchor_pixels(graph, 1200)
        graph = sample_graph()
        del graph['objective_bases']
        with self.assertRaisesRegex(ValueError, 'objective_bases'):
            renderer._render(
                graph, Image.new('RGB', (64, 64), (0, 0, 0)), 1200)


if __name__ == '__main__':
    unittest.main()
