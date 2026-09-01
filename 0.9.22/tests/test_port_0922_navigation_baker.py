import importlib.util
import hashlib
import copy
import json
import shutil
import struct
import sys
import tempfile
import types
import math
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / '0.9.22' / 'tools'
sys.path.insert(0, str(TOOLS))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


space = load_module('space_bin_0922')
baker = load_module('bake_navigation_0922')
packed = load_module('packed_xml')


def section(name, version, offset, size, rows=0):
    return struct.pack('<4s5I', name.encode('ascii'), version, offset, 0, size, rows)


def compiled_space(sections):
    header_size = 24 * (1 + len(sections))
    offset = header_size
    directory = []
    payloads = []
    for name, version, payload in sections:
        directory.append(section(name, version, offset, len(payload)))
        payloads.append(payload)
        offset += len(payload)
    return (struct.pack('<4s5I', b'BWTB', 1, header_size, 0, 0, len(sections)) +
            b''.join(directory) + b''.join(payloads))


class CompiledSpace0922Test(unittest.TestCase):

    def test_ordinary_routes_use_one_canonical_reversible_polyline(self):
        graph = {'bake': {'soft_route_fallbacks': []}}
        routes = {
            '1': [
                {'id': 'through', 'capacity': 5, 'risk': 0.6,
                 'role_weights': {'scout': 1.0},
                 'waypoints': [[0.0, 0.0, False],
                               [4.0, 4.0, True],
                               [8.0, 8.0, False]]},
                {'id': 'rear_guard', 'capacity': 1, 'risk': 0.1,
                 'role_weights': {'artillery': 1.0},
                 'waypoints': [[0.0, 0.0, False],
                               [-4.0, 0.0, True]]},
            ],
            '2': [
                {'id': 'through', 'capacity': 5, 'risk': 0.6,
                 'role_weights': {'scout': 1.0},
                 'waypoints': [[8.0, 8.0, False],
                               [8.0, 4.0, True],
                               [0.0, 0.0, False]]},
                {'id': 'rear_guard', 'capacity': 1, 'risk': 0.1,
                 'role_weights': {'artillery': 1.0},
                 'waypoints': [[8.0, 8.0, False],
                               [12.0, 8.0, True]]},
            ],
        }

        result = baker.canonicalize_reversible_routes(graph, routes)

        self.assertEqual(
            [[8.0, 8.0, False], [4.0, 4.0, True],
             [0.0, 0.0, False]],
            result['2'][0]['waypoints'])
        self.assertEqual(
            [[8.0, 8.0, False], [12.0, 8.0, True]],
            result['2'][1]['waypoints'])
        self.assertEqual(
            ['through'], graph['bake']['canonical_reversible_routes'])

    def test_directional_fallback_mismatch_is_rejected(self):
        graph = {'bake': {'soft_route_fallbacks': ['1:through']}}
        route = {'id': 'through', 'capacity': 1, 'risk': 0.5,
                 'role_weights': {},
                 'waypoints': [[0.0, 0.0, False],
                               [4.0, 4.0, False]]}
        reverse = dict(route)
        reverse['waypoints'] = list(reversed(route['waypoints']))
        with self.assertRaisesRegex(
                baker.UnsafeBakeInputError,
                'reversible tactical fallback differs'):
            baker.canonicalize_reversible_routes(
                graph, {'1': [route], '2': [reverse]})

    def test_final_validation_rejects_a_canonical_reverse_on_one_way_links(self):
        legacy = baker._legacy_baker()
        graph = {
            'cell_size': 4.0,
            'origin': (0.0, 0.0),
            'bounds': (0.0, 0.0, 8.0, 0.0),
            'width': 3,
            'height': 1,
            'heights_mm': [0, 0, 0],
            # East-only links let team one cross the fixture but deliberately
            # make the same sampled corridor invalid in reverse.
            'links': [1 << 4, 1 << 4, 0],
            'hazards': [0, 0, 0],
            'directions': [list(direction)
                           for direction in legacy.DIRECTIONS],
            'bake': {'soft_route_fallbacks': []},
        }
        first = {
            'id': 'through', 'capacity': 1, 'risk': 0.5,
            'role_weights': {},
            'waypoints': [[0.0, 0.0, False],
                          [4.0, 0.0, True],
                          [8.0, 0.0, False]],
        }
        second = dict(first)
        second['waypoints'] = list(reversed(first['waypoints']))
        routes = {'1': [first], '2': [second]}

        graph['routes'] = baker.canonicalize_reversible_routes(
            graph, routes)

        with self.assertRaisesRegex(
                ValueError, 'route segment is disconnected'):
            legacy.validate_graph(
                graph, {'bases': ((0.0, 0.0), (8.0, 0.0)),
                        'routes': (), 'anchors': ()})

    def test_mature_navigation_baker_is_pinned_inside_the_port(self):
        path = Path(baker.LEGACY_BAKER)
        self.assertTrue(path.is_file())
        self.assertEqual(
            Path(baker.LEGACY_BASELINE_ROOT) / 'tools' /
            'bake_navigation.py', path)
        self.assertEqual(
            baker.LEGACY_BAKER_SHA256,
            hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(
            'f5b0173c296cd36753a5866ba5e6f2119e3edb25',
            baker.LEGACY_BAKER_COMMIT)
        self.assertEqual(7, len(baker.LEGACY_BASELINE_SHA256))
        baseline_root = Path(baker.LEGACY_BASELINE_ROOT)
        self.assertEqual(
            set(baker.LEGACY_BASELINE_SHA256),
            {str(path.relative_to(baseline_root))
             for path in baseline_root.rglob('*') if path.is_file()})
        for relative_path, digest in baker.LEGACY_BASELINE_SHA256.items():
            baseline_path = baseline_root / Path(relative_path)
            self.assertEqual(
                digest, hashlib.sha256(baseline_path.read_bytes()).hexdigest())

    @staticmethod
    def _vehicle_chassis_visual(minimum, maximum):
        bounds = packed.PackedElement(children=[
            (b'min', packed.PackedValue(
                packed.TYPE_VECTOR, struct.pack('<3f', *minimum))),
            (b'max', packed.PackedValue(
                packed.TYPE_VECTOR, struct.pack('<3f', *maximum))),
        ])
        root = packed.PackedElement(children=[
            (b'boundingBox', packed.PackedValue(
                packed.TYPE_ELEMENT, bounds)),
        ])
        return packed.write_packed_xml(root)

    def test_vehicle_spawn_envelope_is_measured_from_pinned_chassis_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary) / 'res' / 'packages'
            packages.mkdir(parents=True)
            with zipfile.ZipFile(packages / 'vehicles_level_06.pkg', 'w') as archive:
                archive.writestr(
                    'vehicles/british/Long/collision_client/'
                    'Chassis.visual_processed',
                    self._vehicle_chassis_visual(
                        (-1.4, 0.0, -5.46), (1.4, 1.6, 4.7)))
            with zipfile.ZipFile(packages / 'vehicles_level_07.pkg', 'w') as archive:
                archive.writestr(
                    'vehicles/japan/Wide/collision_client/'
                    'Chassis.visual_processed',
                    self._vehicle_chassis_visual(
                        (-2.24, 0.0, -4.9), (2.24, 1.7, 4.6)))
            # HD render packages do not own the collision-client body and must
            # not change the deterministic standard-resource measurement.
            with zipfile.ZipFile(
                    packages / 'vehicles_level_10_hd.pkg', 'w') as archive:
                archive.writestr('ignored', b'')

            envelope = baker.representative_vehicle_chassis_envelope(
                temporary)

        self.assertAlmostEqual(2.24, envelope['half_width'], places=5)
        self.assertAlmostEqual(5.46, envelope['half_length'], places=5)
        self.assertIn('/Wide/', envelope['width_source'])
        self.assertIn('/Long/', envelope['length_source'])
        self.assertEqual(2, envelope['resources_scanned'])

    def test_spawn_clearance_uses_yaw_oriented_maximum_chassis_obb(self):
        obstacles = types.SimpleNamespace(
            raster_size=1.0, cells={(0, 4): [0.65, 2.4]})
        legacy = types.SimpleNamespace(
            VEHICLE_GROUND_CLEARANCE=0.65,
            VEHICLE_CLEARANCE_HEIGHT=2.4)

        self.assertTrue(baker.spawn_obstacle_obb_blocked(
            obstacles, 0.0, 0.0, 0.0, 0.0, 2.24, 5.46, legacy))
        self.assertFalse(baker.spawn_obstacle_obb_blocked(
            obstacles, 0.0, 0.0, 0.0, math.pi / 2.0,
            2.24, 5.46, legacy))

    def test_spawn_clearance_rejects_overlapping_maximum_chassis_obbs(self):
        first = (0.0, 0.0, 0.0, 0.0)
        self.assertTrue(baker.spawn_obbs_overlap(
            first, (0.0, 0.0, 10.0, 0.0), 2.24, 5.46))
        self.assertFalse(baker.spawn_obbs_overlap(
            first, (0.0, 0.0, 12.0, 0.0), 2.24, 5.46))
        self.assertFalse(baker.spawn_obbs_overlap(
            first, (14.0, 0.0, 0.0, 0.0), 2.24, 5.46))

    def test_runtime_loader_ignores_manifest_identity_and_validates_graph(self):
        graph = ROOT / '0.9.22' / 'navgraphs' / '06_ensk.json'
        self.assertTrue(graph.is_file(), 'baked Ensk graph is missing')
        loader_path = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                       'client' / 'gui' / 'mods' / 'offline_lan_0922' /
                       'prebaked_navigation.py')
        spec = importlib.util.spec_from_file_location('prebaked_navigation_test',
                                                      loader_path)
        loader = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'navgraphs'
            directory.mkdir()
            shutil.copy2(graph, directory / graph.name)
            manifest_path = directory / 'manifest.json'
            shutil.copy2(
                ROOT / '0.9.22' / 'navgraphs' / 'manifest.json',
                manifest_path)
            manifest = json.loads(manifest_path.read_text())
            manifest['game_version'] = 'locally-repacked-client'
            next(record for record in manifest['maps']
                 if record['map'] == '06_ensk')['sha256'] = 'stale metadata'
            manifest_path.write_text(json.dumps(manifest))
            graph_path = directory / graph.name
            graph_value = json.loads(graph_path.read_text())
            graph_value['game_version'] = 'locally-repacked-client'
            graph_path.write_text(json.dumps(graph_value))
            package_names = ('gui', 'gui.mods',
                             'gui.mods.offline_lan_0922')
            config_name = 'gui.mods.offline_lan_0922.config'
            schema_name = 'gui.mods.offline_lan_0922.navigation_graph_schema'
            names = package_names + (config_name, schema_name)
            saved = {name: sys.modules.get(name) for name in names}
            try:
                for name in package_names:
                    package = types.ModuleType(name)
                    package.__path__ = []
                    sys.modules[name] = package
                sys.modules[package_names[-1]].__path__ = [
                    str(loader_path.parent)]
                config = types.ModuleType(config_name)
                config.CONFIG_PATH = str(Path(temporary) / 'config.json')
                sys.modules[config_name] = config
                spec.loader.exec_module(loader)
                loaded = loader.load_graph('06_ensk')

                manifest = json.loads(manifest_path.read_text())
                selected = next(
                    record for record in manifest['maps']
                    if record['map'] == '06_ensk')
                selected['file'] = 'missing.json'
                manifest_path.write_text(json.dumps(manifest))
                loaded_without_manifest_identity = loader.load_graph(
                    '06_ensk')

                graph_value = json.loads(graph_path.read_text())
                graph_value['format'] = 'invalid-navigation-graph'
                graph_path.write_text(json.dumps(graph_value))
                with self.assertRaises(ValueError):
                    loader.load_graph('06_ensk')
            finally:
                for name, previous in saved.items():
                    if previous is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous
        self.assertEqual('offline-lan-0922-navgraph', loaded['format'])
        self.assertEqual(
            'offline-lan-0922-navgraph',
            loaded_without_manifest_identity['format'])
        self.assertGreater(len(loaded['routes']['1']), 0)
        self.assertGreater(loaded['validation']['route_segments'], 0)

    def test_real_lakeville_graph_marks_compiled_water_cells_fatal(self):
        graph = ROOT / '0.9.22' / 'navgraphs' / '07_lakeville.json'
        self.assertTrue(graph.is_file(), 'baked Lakeville graph is missing')
        data = json.loads(graph.read_text())
        cells = data['bake']['water_cell_bounds']
        self.assertEqual(15, len(cells))
        self.assertEqual('BWWa-cell-surface-depth', data['bake']['water_mode'])
        water_indexes = [index for index, hazard in enumerate(data['hazards'])
                         if hazard & 1]
        self.assertGreater(len(water_indexes), 0)
        self.assertEqual(len(water_indexes), data['bake']['rejected_water_nodes'])
        self.assertTrue(all(data['heights_mm'][index] is None
                            for index in water_indexes))
        self.assertGreater(data['validation']['route_segments'], 0)

    def test_real_himmelsdorf_rear_guard_has_connected_locomotion(self):
        graph = ROOT / '0.9.22' / 'navgraphs' / '04_himmelsdorf.json'
        self.assertTrue(graph.is_file(), 'baked Himmelsdorf graph is missing')
        data = json.loads(graph.read_text())

        for team in ('1', '2'):
            route = next(value for value in data['routes'][team]
                         if value['id'] == 'rear_guard')
            self.assertGreaterEqual(len(route['waypoints']), 2)
            self.assertLessEqual(len(route['waypoints']), 16)
            self.assertTrue(route['waypoints'][-1][2])
            start = route['waypoints'][0]
            anchor = data['spawn_anchors'][int(team) - 1]
            self.assertLessEqual(
                ((start[0] - anchor[0]) ** 2 +
                 (start[1] - anchor[1]) ** 2) ** 0.5,
                data['cell_size'] * 1.5)

    def test_real_dday_graph_uses_packed_ctf_bases_and_valid_routes(self):
        graph = ROOT / '0.9.22' / 'navgraphs' / '101_dday.json'
        self.assertTrue(graph.is_file(), 'baked D-Day graph is missing')
        data = json.loads(graph.read_text())

        self.assertEqual([-400.0, -500.0, 600.0, 500.0], data['bounds'])
        self.assertAlmostEqual(149.9971923828125,
                               data['objective_bases'][0][0], places=5)
        self.assertAlmostEqual(-403.4408264160156,
                               data['objective_bases'][0][1], places=5)
        self.assertAlmostEqual(149.6625213623047,
                               data['objective_bases'][1][0], places=5)
        self.assertAlmostEqual(400.3866271972656,
                               data['objective_bases'][1][1], places=5)
        self.assertEqual([[], []], data['ctf_spawn_points'])
        self.assertEqual('ctf objectives projected onto validated graph',
                         data['spawn_anchor_source'])
        self.assertLess(data['bake']['maximum_route_projection'], 8.0)
        self.assertEqual([], data['bake']['soft_route_fallbacks'])
        self.assertGreater(data['bake']['rejected_obstacle_nodes'], 0)
        self.assertGreater(data['bake']['rejected_water_nodes'], 0)
        self.assertEqual(90, data['validation']['route_segments'])
        for team, start, finish in (
                ('1', data['spawn_anchors'][0], data['spawn_anchors'][1]),
                ('2', data['spawn_anchors'][1], data['spawn_anchors'][0])):
            self.assertEqual({'beach', 'village', 'cliff'},
                             {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_real_thepit_routes_start_after_verified_one_way_ingress(self):
        graph = ROOT / '0.9.22' / 'navgraphs' / '100_thepit.json'
        self.assertTrue(graph.is_file(), 'baked The Pit graph is missing')
        data = json.loads(graph.read_text())

        self.assertTrue(data['bake']['directed_spawn_ingress'])
        self.assertEqual([], data['bake']['soft_route_fallbacks'])
        for team in ('1', '2'):
            ingress = data['validation']['spawn_ingress'][team]
            self.assertTrue(ingress['forward_connected'])
            self.assertTrue(ingress['reverse_links_absent'])
            self.assertGreater(ingress['one_way_links'], 0)
            start = data['spawn_anchors'][int(team) - 1]
            finish = data['spawn_anchors'][2 - int(team)]
            self.assertEqual(
                {'rim_west', 'pit', 'rim_east'},
                {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_real_eiffel_graph_uses_ctf_objectives_and_mature_obstacle_rules(self):
        graph = (ROOT / '0.9.22' / 'navgraphs' /
                 '112_eiffel_tower_ctf.json')
        self.assertTrue(graph.is_file(), 'baked Eiffel graph is missing')
        data = json.loads(graph.read_text())

        self.assertEqual([-400.0, -400.0, 400.0, 400.0], data['bounds'])
        self.assertAlmostEqual(-346.07440185546875,
                               data['objective_bases'][0][0], places=5)
        self.assertAlmostEqual(-22.52288055419922,
                               data['objective_bases'][0][1], places=5)
        self.assertAlmostEqual(341.26910400390625,
                               data['objective_bases'][1][0], places=5)
        self.assertAlmostEqual(-19.86382293701172,
                               data['objective_bases'][1][1], places=5)
        self.assertEqual([[], []], data['ctf_spawn_points'])
        self.assertEqual('ctf objectives projected onto validated graph',
                         data['spawn_anchor_source'])
        self.assertGreater(data['bake']['soft_model_instances'], 0)
        self.assertGreater(data['bake']['local_obstacle_instances'], 0)
        self.assertGreater(data['bake']['bridge_model_instances'], 0)
        self.assertGreater(data['bake']['bridge_surface_triangles'], 0)
        # Exact BSP2 v2 decoding exposes the authored Eiffel collision that
        # the legacy-header interpretation skipped.  Pin the resulting graph
        # census so a return to the falsely sparse obstacle raster cannot pass
        # behind the old 90 percent retained-node threshold.
        self.assertEqual(29888, data['bake']['source_navigable_nodes'])
        self.assertEqual(24418, data['bake']['retained_nodes'])
        self.assertEqual(5470, data['bake']['pruned_nodes'])
        self.assertEqual(384, data['bake']['source_components'])
        self.assertEqual(287775, data['bake']['obstacle_raster_cells'])
        self.assertEqual(0, data['bake']['skipped_models'])
        self.assertAlmostEqual(0.81698,
                               data['bake']['retained_fraction'], places=5)
        self.assertEqual(1, data['validation']['components'])
        self.assertEqual(90, data['validation']['route_segments'])
        self.assertLessEqual(max(data['validation']['spawn_start_reach_metres']),
                             data['cell_size'])
        for team, start, finish in (
                ('1', data['spawn_anchors'][0], data['spawn_anchors'][1]),
                ('2', data['spawn_anchors'][1], data['spawn_anchors'][0])):
            self.assertEqual({'tower_west', 'center', 'tower_east'},
                             {route['id'] for route in data['routes'][team]})
            for route in data['routes'][team]:
                self.assertEqual(16, len(route['waypoints']))
                self.assertEqual(start, route['waypoints'][0][:2])
                self.assertEqual(finish, route['waypoints'][-1][:2])

    def test_every_shipped_map_has_a_complete_validated_spawn_formation(self):
        graph_root = ROOT / '0.9.22' / 'navgraphs'
        paths = sorted(path for path in graph_root.glob('*.json')
                       if path.name != 'manifest.json')
        self.assertEqual(41, len(paths))
        for path in paths:
            with self.subTest(map=path.stem):
                data = json.loads(path.read_text())
                self.assertEqual(2, data['version'])
                self.assertEqual({'1', '2'}, set(data['spawn_formations']))
                self.assertEqual(15, len(data['spawn_formations']['1']))
                self.assertEqual(15, len(data['spawn_formations']['2']))
                self.assertTrue(all(len(point) == 4
                                    for team in data['spawn_formations'].values()
                                    for point in team))
                validation = data['validation']
                self.assertEqual(15, validation['spawn_slots_per_team'])
                self.assertGreaterEqual(
                    validation['spawn_minimum_spacing_metres'], 10.5)
                self.assertGreaterEqual(
                    validation['spawn_minimum_team_separation_metres'], 80.0)
                self.assertLessEqual(
                    validation['spawn_maximum_projection_metres'], 32.0)
                self.assertIs(
                    True, validation['spawn_compiled_bsp_obb_clearance'])
                self.assertIs(
                    True, validation['spawn_pairwise_obb_clearance'])
                self.assertAlmostEqual(
                    2.239622,
                    validation['spawn_vehicle_half_width_metres'], places=6)
                self.assertAlmostEqual(
                    5.462265,
                    validation['spawn_vehicle_half_length_metres'], places=6)
                self.assertEqual(
                    534, validation['spawn_vehicle_resources_scanned'])
                self.assertEqual(
                    'vehicles/japan/J24_Mi_To_130_tons/collision_client/'
                    'Chassis.visual_processed',
                    validation['spawn_vehicle_width_source'])
                self.assertEqual(
                    'vehicles/british/GB63_TOG_II/collision_client/'
                    'Chassis.visual_processed',
                    validation['spawn_vehicle_length_source'])
                self.assertNotIn(
                    'fallback', data['spawn_formation_source'].lower())

    def test_every_shipped_map_has_exact_tactical_route_contracts(self):
        graph_root = ROOT / '0.9.22' / 'navgraphs'
        paths = sorted(path for path in graph_root.glob('*.json')
                       if path.name != 'manifest.json')
        self.assertEqual(41, len(paths))
        roles = {
            'brawler', 'support', 'flanker',
            'sniper', 'scout', 'artillery',
        }
        for path in paths:
            with self.subTest(map=path.stem):
                data = json.loads(path.read_text())
                self.assertEqual(1, data['validation']['components'])
                self.assertEqual(1.0, data['validation']['largest_fraction'])
                self.assertEqual(
                    sum(len(route['waypoints']) - 1
                        for team in ('1', '2')
                        for route in data['routes'][team]),
                    data['validation']['route_segments'])

                soft_routes = set(data['bake']['soft_route_fallbacks'])
                self.assertEqual(
                    soft_routes,
                    set(data['bake']['soft_route_fallback_causes']))
                self.assertTrue(all(
                    isinstance(cause, str) and cause
                    for cause in
                    data['bake']['soft_route_fallback_causes'].values()))
                team_metadata = []
                route_keys = set()
                for team in ('1', '2'):
                    routes = data['routes'][team]
                    route_ids = [route['id'] for route in routes]
                    if path.stem == '04_himmelsdorf':
                        self.assertEqual(
                            {'banana', 'hill', 'rail', 'rear_guard'},
                            set(route_ids))
                    elif path.stem == '06_ensk':
                        self.assertEqual(
                            {'west_city', 'east_field'}, set(route_ids))
                        self.assertEqual(
                            [7, 7],
                            [route['capacity'] for route in routes])
                    else:
                        self.assertEqual(3, len(routes))
                    self.assertEqual(len(route_ids), len(set(route_ids)))
                    own = data['spawn_anchors'][int(team) - 1]
                    enemy = data['spawn_anchors'][2 - int(team)]
                    metadata = []
                    for route in routes:
                        route_key = '%s:%s' % (team, route['id'])
                        route_keys.add(route_key)
                        self.assertEqual(own, route['waypoints'][0][:2])
                        self.assertGreaterEqual(route['capacity'], 1)
                        self.assertGreaterEqual(route['risk'], 0.0)
                        self.assertLessEqual(route['risk'], 1.0)
                        self.assertEqual(roles, set(route['role_weights']))
                        self.assertTrue(all(
                            0.0 <= value <= 1.0
                            for value in route['role_weights'].values()))
                        self.assertGreaterEqual(len(route['waypoints']), 2)
                        self.assertLessEqual(len(route['waypoints']), 16)
                        for x, z, unused_hold in route['waypoints']:
                            column = int(round(
                                (x - data['origin'][0]) / data['cell_size']))
                            row = int(round(
                                (z - data['origin'][1]) / data['cell_size']))
                            self.assertGreaterEqual(column, 0)
                            self.assertLess(column, data['width'])
                            self.assertGreaterEqual(row, 0)
                            self.assertLess(row, data['height'])
                            index = row * data['width'] + column
                            self.assertIsNotNone(data['heights_mm'][index])
                            self.assertEqual(0, data['hazards'][index] & 3)
                        if (route.get('terminal_hold', False) or
                                route['id'] == 'rear_guard'):
                            self.assertTrue(route['waypoints'][-1][2])
                        else:
                            self.assertEqual(enemy, route['waypoints'][-1][:2])
                        metadata.append((
                            route['id'], route['capacity'], route['risk'],
                            route['role_weights'],
                            bool(route.get('terminal_hold', False))))
                    for role in roles - {'artillery'}:
                        self.assertGreater(
                            max(route['role_weights'][role]
                                for route in routes), 0.0)
                    team_metadata.append(metadata)
                self.assertEqual(team_metadata[0], team_metadata[1])
                self.assertTrue(soft_routes.issubset(route_keys))

    def test_reads_0920_bwt2_chunk_vector(self):
        settings = struct.pack('<f4i3I', 100.0, -4, 3, -4, 3, 0, 0, 0)
        chunks = struct.pack('<II', 8, 2)
        chunks += struct.pack('<Ihh', 1, -4, -4)
        chunks += struct.pack('<Ihh', 2, 3, 3)
        data = compiled_space((('BWT2', 2, struct.pack('<I', 32) + settings + chunks),))

        terrain = space.CompiledSpace(data).terrain_info_0920()

        self.assertEqual(100.0, terrain.chunk_size)
        self.assertEqual((-4, 3, -4, 3), terrain.bounds)
        self.assertEqual(((1, -4, -4), (2, 3, 3)), terrain.chunks)

    def test_truncated_or_unknown_terrain_layout_fails_closed(self):
        bad = compiled_space((('BWT2', 2, struct.pack('<II', 31, 0)),))
        with self.assertRaises(space.CompiledSpaceError):
            space.CompiledSpace(bad).terrain_info_0920()

    def test_navigation_requires_decoded_collision_and_water(self):
        data = compiled_space(tuple((name, 2, b'') for name in
                                    ('BWSG', 'BSGD', 'BWWa', 'WTCP')))
        with self.assertRaises(space.UnsafeBakeInputError) as error:
            space.CompiledSpace(data).require_safe_navigation_sources()
        self.assertIn('refusing', str(error.exception))

    def test_compiled_soft_destructibles_skip_only_falling_and_fragile(self):
        transforms = [tuple([float(index)] + [0.0] * 15)
                      for index in range(4)]

        class ModelInstances(object):
            _data = {'transforms': transforms}

            @staticmethod
            def model_ids():
                return iter((0, 1, 2, 3))

        class Strings(object):
            @staticmethod
            def get(value):
                return 'objects/type%d.primitives/indices' % value

        model_data = {
            'model_info_items': [
                {'type': 0}, {'type': 1}, {'type': 2}, {'type': 3}],
            'models_loddings': [{'lod_begin': index}
                                for index in range(4)],
            'lod_renders': [
                {'render_set_begin': index, 'render_set_end': index}
                for index in range(4)],
            'renders': [{'prims_name_fnv': index} for index in range(4)],
        }
        compiled = types.SimpleNamespace(sections={
            'BSMI': ModelInstances(),
            'BSMO': types.SimpleNamespace(_data=model_data),
            'BWST': Strings(),
        })

        keys, counts = baker.compiled_soft_destructible_instances(compiled)

        self.assertEqual({
            ('objects/type1.primitives_processed', transforms[1]),
            ('objects/type2.primitives_processed', transforms[2]),
        }, keys)
        self.assertEqual({
            'falling': 1,
            'fragile': 1,
            'structures_preserved': 1,
            'primitive_transform_keys': 2,
        }, counts)

    def test_compiled_local_collision_bounds_preserve_low_obstacle_rule(self):
        transforms = [tuple([float(index)] + [0.0] * 15)
                      for index in range(2)]

        class ModelInstances(object):
            _data = {'transforms': transforms}

            @staticmethod
            def model_ids():
                return iter((0, 1))

        class Strings(object):
            @staticmethod
            def get(value):
                return 'objects/type%d.primitives/indices' % value

        model_data = {
            'models_colliders': [
                {'collision_bounds_min': (0.0, -0.1, 0.0),
                 'collision_bounds_max': (2.0, 0.5, 2.0)},
                {'collision_bounds_min': (0.0, -0.1, 0.0),
                 'collision_bounds_max': (2.0, 0.56, 2.0)},
            ],
            'models_loddings': [{'lod_begin': index}
                                for index in range(2)],
            'lod_renders': [
                {'render_set_begin': index, 'render_set_end': index}
                for index in range(2)],
            'renders': [{'prims_name_fnv': index} for index in range(2)],
        }
        compiled = types.SimpleNamespace(sections={
            'BSMI': ModelInstances(),
            'BSMO': types.SimpleNamespace(_data=model_data),
            'BWST': Strings(),
        })

        keys, counts = baker.compiled_local_obstacle_instances(compiled, 0.65)

        self.assertEqual({
            ('objects/type0.primitives_processed', transforms[0]),
        }, keys)
        self.assertEqual({
            'instances': 1,
            'primitive_transform_keys': 1,
            'maximum_local_height': 0.65,
        }, counts)

    def test_compiled_bridge_keeps_walkable_deck_and_blocks_body(self):
        deck = ((0.0, 2.0, 0.0), (4.0, 2.0, 0.0), (0.0, 2.0, 4.0))
        body = ((0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0))

        class Obstacles(object):
            def __init__(self):
                self.bridge_instance_count = 0
                self.bridge_surface_triangle_count = 0
                self.surfaces = []
                self.blockers = []

            def _bridge_deck_triangles(self, triangles):
                return {id(triangles[0])}

            def _raster_surface_triangle(self, triangle):
                self.surfaces.append(triangle)

            def _raster_triangle(self, triangle):
                self.blockers.append(triangle)

        obstacles = Obstacles()
        legacy = types.SimpleNamespace(
            _is_bridge_model=lambda name: 'bridge' in name.lower())

        baker._raster_compiled_collision_instance(
            obstacles, 'content/WideBridge.primitives_processed',
            [deck, body], legacy)

        self.assertEqual(1, obstacles.bridge_instance_count)
        self.assertEqual(1, obstacles.bridge_surface_triangle_count)
        self.assertEqual([deck], obstacles.surfaces)
        self.assertEqual([body], obstacles.blockers)

    @staticmethod
    def _bsp2_v2(triangles, node_count=1, shared_count=0,
                 plane_size=16, triangle_size=40, node_size=40):
        values = [coordinate
                  for triangle in triangles
                  for point in triangle
                  for coordinate in point]
        minimum = tuple(min(point[axis]
                            for triangle in triangles
                            for point in triangle)
                        if triangles else 0.0
                        for axis in range(3))
        maximum = tuple(max(point[axis]
                            for triangle in triangles
                            for point in triangle)
                        if triangles else 0.0
                        for axis in range(3))
        header = struct.pack(
            '<4I3I6f2I', 0x02505342, plane_size, triangle_size, node_size,
            len(triangles), node_count, shared_count,
            *(minimum + maximum + (0, 0)))
        triangle_data = b''.join(
            # WorldTriangle::Flags flags_, WorldTriangle::Padding padding_.
            struct.pack('<9fHH', *values[index:index + 9], 0, 0)
            for index in range(0, len(values), 9))
        return (header + triangle_data + b'\x00' * (shared_count * 4) +
                b'\x00' * (node_count * node_size))

    def test_bsp2_v2_uses_exact_current_layout_not_legacy_header_counts(self):
        triangles = [
            ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)),
            ((-1.0, -2.0, -3.0), (-4.0, -5.0, -6.0),
             (-7.0, -8.0, -9.0)),
        ]
        legacy = types.SimpleNamespace(
            _bsp_triangles=lambda unused_section, unused_names, unused_flags:
            self.fail('version 2 must not use the legacy parser'))

        decoded = baker._bsp_triangles_0922(
            self._bsp2_v2(triangles, node_count=3, shared_count=2), legacy)

        self.assertEqual(triangles, decoded)

    def test_bsp2_v2_rejects_wrong_abi_counts_and_trailing_bytes(self):
        triangle = ((0.0, 0.0, 0.0),
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0))
        legacy = types.SimpleNamespace(_bsp_triangles=None)
        wrong_triangle_count = bytearray(self._bsp2_v2([triangle]))
        struct.pack_into('<I', wrong_triangle_count, 16, 2)
        malformed = (
            self._bsp2_v2([triangle], plane_size=12),
            self._bsp2_v2([triangle], node_count=0),
            bytes(wrong_triangle_count),
            self._bsp2_v2([triangle])[:-1],
            self._bsp2_v2([triangle]) + b'\x00',
        )

        for section_data in malformed:
            with self.subTest(length=len(section_data)):
                with self.assertRaises(ValueError):
                    baker._bsp_triangles_0922(section_data, legacy)

    def test_bsp2_legacy_layout_remains_compatible(self):
        triangle = ((0.0, 1.0, 2.0),
                    (3.0, 4.0, 5.0),
                    (6.0, 7.0, 8.0))
        section_data = (struct.pack('<4I', 0x00505342, 1, 1, 0) +
                        struct.pack('<9fI',
                                    *(triangle[0] + triangle[1] + triangle[2] +
                                      (0,))))
        legacy = types.SimpleNamespace(
            _bsp_triangles=lambda value, names, flags:
            [('legacy', value, names, flags)])

        decoded = baker._bsp_triangles_0922(section_data, legacy)

        self.assertEqual([('legacy', section_data, (), {})], decoded)

    def test_great_wall_4m_grid_phase_changes_only_x_and_is_recorded(self):
        original = (-500.0, -500.0, 500.0, 500.0)
        self.assertEqual(
            original,
            baker._target_expanded_bounds(
                '59_asia_great_wall', original, 3.0, original))
        self.assertEqual(
            original,
            baker._target_expanded_bounds(
                '07_lakeville', original, 4.0, original))

        phased = baker._target_expanded_bounds(
            '59_asia_great_wall', original, 4.0, original)

        self.assertEqual((-502.0, -500.0, 502.0, 500.0), phased)
        self.assertEqual(original[1::2], phased[1::2])
        graph = {
            'cell_size': 4.0,
            'bounds': list(phased),
            'origin': [-500.0, -498.0],
            'width': 251,
            'height': 250,
            'directions': [
                [-1, -1], [0, -1], [1, -1], [-1, 0],
                [1, 0], [-1, 1], [0, 1], [1, 1],
            ],
            'heights_mm': [None] * (251 * 250),
            'hazards': [0] * (251 * 250),
            'links': [0] * (251 * 250),
            'bake': {},
        }
        def index(x, z):
            column = int((x + 500.0) / 4.0)
            row = int((z + 498.0) / 4.0)
            return row * 251 + column
        north = 1 << graph['directions'].index([0, 1])
        south = 1 << graph['directions'].index([0, -1])
        for z in (-150.0, -146.0, -142.0):
            graph['heights_mm'][index(404.0, z)] = 1000
        graph['links'][index(404.0, -150.0)] |= north
        graph['links'][index(404.0, -146.0)] |= north | south
        graph['links'][index(404.0, -142.0)] |= south
        self.assertTrue(baker._record_target_grid_phase(
            graph, '59_asia_great_wall', 4.0))
        self.assertEqual(list(original), graph['bounds'])
        self.assertEqual(-500.0, graph['origin'][0])
        self.assertEqual(
            500.0,
            graph['origin'][0] +
            (graph['width'] - 1) * graph['cell_size'])
        self.assertEqual({
            'map': '59_asia_great_wall',
            'axis': 'x',
            'cell_size': 4.0,
            'original_expanded_bounds': list(original),
            'applied_sampling_bounds': list(phased),
            'public_gameplay_bounds': list(original),
            'origin': [-500.0, -498.0],
            'dimensions': [251, 250],
            'passage_x': 404.0,
            'passage_x_index': 226,
            'passage_nodes': [
                [404.0, -150.0], [404.0, -146.0], [404.0, -142.0],
            ],
            'reason': '#1513 gatehouse passage requires an x=404m graph centre',
        }, graph['bake']['grid_phase_override'])

    def test_great_wall_4m_grid_phase_rejects_any_contract_drift(self):
        original = (-500.0, -500.0, 500.0, 500.0)
        with self.assertRaises(space.UnsafeBakeInputError):
            baker._target_expanded_bounds(
                '59_asia_great_wall', (-499.0,) + original[1:],
                4.0, original)
        with self.assertRaises(space.UnsafeBakeInputError):
            baker._target_expanded_bounds(
                '59_asia_great_wall', original, 4.0,
                (-504.0,) + original[1:])

        valid = {
            'cell_size': 4.0,
            'bounds': [-502.0, -500.0, 502.0, 500.0],
            'origin': [-500.0, -498.0],
            'width': 251,
            'height': 250,
            'directions': [
                [-1, -1], [0, -1], [1, -1], [-1, 0],
                [1, 0], [-1, 1], [0, 1], [1, 1],
            ],
            'heights_mm': [None] * (251 * 250),
            'hazards': [0] * (251 * 250),
            'links': [0] * (251 * 250),
            'bake': {},
        }
        def index(x, z):
            column = int((x + 500.0) / 4.0)
            row = int((z + 498.0) / 4.0)
            return row * 251 + column
        north = 1 << valid['directions'].index([0, 1])
        south = 1 << valid['directions'].index([0, -1])
        for z in (-150.0, -146.0, -142.0):
            valid['heights_mm'][index(404.0, z)] = 1000
        valid['links'][index(404.0, -150.0)] |= north
        valid['links'][index(404.0, -146.0)] |= north | south
        valid['links'][index(404.0, -142.0)] |= south
        mutations = (
            ('cell_size', 3.999),
            ('bounds', [-500.0, -500.0, 500.0, 500.0]),
            ('origin', [-498.0, -498.0]),
            ('width', 250),
            ('height', 251),
            ('bake', None),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                graph = dict(valid)
                graph[key] = value
                with self.assertRaises(space.UnsafeBakeInputError):
                    baker._record_target_grid_phase(
                        graph, '59_asia_great_wall', 4.0)

        passage_mutations = ('node', 'hazard', 'link', 'side')
        for name in passage_mutations:
            with self.subTest(passage=name):
                graph = dict(valid)
                graph['heights_mm'] = list(valid['heights_mm'])
                graph['hazards'] = list(valid['hazards'])
                graph['links'] = list(valid['links'])
                graph['bake'] = {}
                if name == 'node':
                    graph['heights_mm'][index(404.0, -146.0)] = None
                elif name == 'hazard':
                    graph['hazards'][index(404.0, -146.0)] = 2
                elif name == 'link':
                    graph['links'][index(404.0, -146.0)] &= ~north
                else:
                    graph['heights_mm'][index(400.0, -146.0)] = 1000
                with self.assertRaises(space.UnsafeBakeInputError):
                    baker._record_target_grid_phase(
                        graph, '59_asia_great_wall', 4.0)

    def test_bwwa_cell_ranges_are_half_open_at_exact_boundary(self):
        record = {'start_id': 0, 'end_id': 2}
        self.assertEqual([(record, 'a'), (record, 'b')],
                         baker.bwwa_regions([record], ['a', 'b']))
        with self.assertRaises(space.UnsafeBakeInputError):
            baker.bwwa_regions([{'start_id': 0, 'end_id': 3}], ['a', 'b'])

    def test_bwwa_cells_transform_from_record_local_to_world_space(self):
        record = {'start_id': 0, 'end_id': 1, 'position': (10.0, -2.0, 20.0),
                  'orientation': 0.0}
        self.assertEqual([(record, (10.0, 0.0, 20.0, 14.0, 0.0, 26.0))],
                         baker.bwwa_world_regions([record], [(0, 0, 0, 4, 0, 6)]))

    def test_bwwa_rotated_aabb_corner_is_not_water(self):
        record = {'start_id': 0, 'end_id': 1, 'position': (0.0, 0.0, 0.0),
                  'orientation': math.pi / 4.0}
        cell = (0.0, 0.0, 0.0, 2.0, 0.0, 2.0)
        unused_record, bounds = baker.bwwa_world_regions([record], [cell])[0]
        self.assertFalse(baker.bwwa_contains(record, cell, bounds[0], bounds[5]))
        self.assertTrue(baker.bwwa_contains(record, cell, 0.0, 0.0))

    @staticmethod
    def _lakeville_corner_graph():
        return {
            'origin': [-122.0, 42.0], 'cell_size': 4.0,
            'width': 2, 'height': 2,
            'directions': [
                [-1, -1], [0, -1], [1, -1], [-1, 0],
                [1, 0], [-1, 1], [0, 1], [1, 1],
            ],
            'heights_mm': [None, 10000, 11000, 10000],
            'hazards': [2, 0, 0, 0],
            'links': [0, 0, 0, 0],
        }

    @staticmethod
    def _lakeville_corner_dependencies(segment_clear=None, ground_height=None,
                                        water_depth=None, blocked=None,
                                        edge_clear=None):
        class Terrain(object):
            def water_depth(self, x, z, ground):
                if water_depth is None:
                    return 0.0
                return water_depth(x, z, ground)

        class Obstacles(object):
            def surface_height(self, unused_x, unused_z):
                return None

            def blocked(self, x, z, ground, margin):
                if blocked is None:
                    return False
                return blocked(x, z, ground, margin)

        legacy = types.SimpleNamespace(
            HAZARD_EDGE=2, HAZARD_WATER=1, WATER_DEPTH_LIMIT=0.9,
            BRIDGE_OBSTACLE_MARGIN=1.0, VEHICLE_HALF_WIDTH=2.15,
            MAX_GRADE_UP=0.38, MAX_GRADE_DOWN=0.38,
            _segment_clear=segment_clear or (
                lambda unused_terrain, unused_obstacles,
                unused_start, unused_end: True),
            _ground_height=lambda unused_terrain, unused_obstacles, x, z:
            ground_height(x, z) if ground_height is not None else 10.0,
            _has_safe_edge_clearance=lambda unused_terrain, unused_obstacles,
            x, z, ground: edge_clear(x, z, ground)
            if edge_clear is not None else True,
        )
        return Terrain(), Obstacles(), legacy

    def test_lakeville_corner_link_changes_only_the_proved_two_bits(self):
        graph = self._lakeville_corner_graph()
        segments = []
        samples = []
        terrain, obstacles, legacy = self._lakeville_corner_dependencies(
            segment_clear=lambda unused_terrain, unused_obstacles, start, end:
            segments.append((start, end)) or True,
            edge_clear=lambda x, z, ground:
            samples.append((x, z, ground)) or True,
        )

        added = baker.install_lakeville_narrow_corner_link(
            graph, terrain, obstacles, legacy)

        self.assertEqual(1, added)
        self.assertEqual(2, len(segments))
        self.assertEqual(8, len(samples))
        for first, second in zip(samples[:4], samples[1:4]):
            self.assertLessEqual(
                math.hypot(second[0] - first[0],
                           second[1] - first[1]), 2.0)
        self.assertEqual([0, 1 << 5, 1 << 2, 0], graph['links'])

    def test_lakeville_corner_link_rejects_contract_drift(self):
        cases = ('endpoint', 'corner_hazard', 'one_way', 'water',
                 'blocked', 'edge', 'grade')
        for name in cases:
            with self.subTest(name=name):
                graph = self._lakeville_corner_graph()
                if name == 'endpoint':
                    graph['heights_mm'][1] = None
                if name == 'corner_hazard':
                    graph['hazards'][0] = 3
                calls = []
                terrain, obstacles, legacy = self._lakeville_corner_dependencies(
                    segment_clear=lambda unused_terrain, unused_obstacles,
                    start, unused_end: calls.append(start) or
                    not (name == 'one_way' and len(calls) == 2),
                    water_depth=lambda unused_x, unused_z, unused_ground:
                    1.0 if name == 'water' else 0.0,
                    blocked=lambda unused_x, unused_z, unused_ground,
                    unused_margin: name == 'blocked',
                    edge_clear=lambda unused_x, unused_z, unused_ground:
                    name != 'edge',
                    ground_height=lambda x, unused_z:
                    x * 10.0 if name == 'grade' else 10.0,
                )

                with self.assertRaises(space.UnsafeBakeInputError):
                    baker.install_lakeville_narrow_corner_link(
                        graph, terrain, obstacles, legacy)

                self.assertEqual([0, 0, 0, 0], graph['links'])

    @staticmethod
    def _reviewed_adapter_dependencies(height=None, water=None, blocked=None,
                                       edge=None, surface=None,
                                       segment_clear=None):
        class Terrain(object):
            def height(self, x, z):
                return height(x, z) if height is not None else 0.0

            def water_depth(self, x, z, ground):
                return water(x, z, ground) if water is not None else 0.0

        class Obstacles(object):
            def blocked(self, x, z, ground, margin):
                return (blocked(x, z, ground, margin)
                        if blocked is not None else False)

            def surface_height(self, x, z):
                return surface(x, z) if surface is not None else None

        terrain = Terrain()
        obstacles = Obstacles()
        legacy = types.SimpleNamespace(
            HAZARD_WATER=1,
            HAZARD_EDGE=2,
            HAZARD_SHALLOW_WATER=4,
            SHALLOW_WATER_THRESHOLD=0.15,
            WATER_DEPTH_LIMIT=0.9,
            VEHICLE_HALF_WIDTH=2.15,
            BRIDGE_OBSTACLE_MARGIN=1.0,
            MAX_GRADE_UP=0.38,
            MAX_GRADE_DOWN=0.38,
            _ground_height=lambda current_terrain, current_obstacles, x, z:
            max(value for value in (
                current_terrain.height(x, z),
                current_obstacles.surface_height(x, z))
                if value is not None),
            _has_safe_edge_clearance=lambda unused_terrain, unused_obstacles,
            x, z, ground: edge(x, z, ground) if edge is not None else True,
            _segment_clear=segment_clear or (
                lambda unused_terrain, unused_obstacles,
                unused_start, unused_end: True),
        )
        return terrain, obstacles, legacy

    @staticmethod
    def _reviewed_adapter_graph(origin=(0.0, 0.0), width=3, height=3):
        return {
            'origin': list(origin),
            'cell_size': 4.0,
            'width': width,
            'height': height,
            'directions': [
                [-1, -1], [0, -1], [1, -1], [-1, 0],
                [1, 0], [-1, 1], [0, 1], [1, 1],
            ],
            'heights_mm': [None] * (width * height),
            'hazards': [0] * (width * height),
            'links': [0] * (width * height),
        }

    @staticmethod
    def _adapter_index(graph, point):
        column = int(round(
            (point[0] - graph['origin'][0]) / graph['cell_size']))
        row = int(round(
            (point[1] - graph['origin'][1]) / graph['cell_size']))
        return row * graph['width'] + column

    def test_reviewed_narrow_corner_adds_only_the_safe_diagonal(self):
        graph = self._reviewed_adapter_graph(width=2, height=2)
        graph['heights_mm'][self._adapter_index(graph, (0.0, 0.0))] = 0
        graph['heights_mm'][self._adapter_index(graph, (4.0, 4.0))] = 0
        for point in ((4.0, 0.0), (0.0, 4.0)):
            graph['hazards'][self._adapter_index(graph, point)] = 2
        contract = {
            'id': 'test_safe_diagonal',
            'points': ((0.0, 0.0), (4.0, 4.0)),
            'side_states': {(4.0, 0.0): 2, (0.0, 4.0): 2},
        }
        terrain, obstacles, legacy = self._reviewed_adapter_dependencies()

        record = baker.install_reviewed_narrow_corner_link(
            graph, terrain, obstacles, legacy, contract)

        self.assertEqual('safe_diagonal', record['kind'])
        self.assertEqual(2, record['directed_links_added'])
        self.assertEqual([1 << 7, 0, 0, 1], graph['links'])
        self.assertEqual([0, 2, 2, 0], graph['hazards'])
        self.assertEqual([0, None, None, 0], graph['heights_mm'])

    def test_reviewed_narrow_corner_rejects_side_cell_drift_before_mutation(self):
        graph = self._reviewed_adapter_graph(width=2, height=2)
        graph['heights_mm'][0] = 0
        graph['heights_mm'][3] = 0
        graph['hazards'][1] = 2
        graph['hazards'][2] = 3
        original = copy.deepcopy(graph)
        terrain, obstacles, legacy = self._reviewed_adapter_dependencies()
        contract = {
            'id': 'test_safe_diagonal',
            'points': ((0.0, 0.0), (4.0, 4.0)),
            'side_states': {(4.0, 0.0): 2, (0.0, 4.0): 2},
        }

        with self.assertRaises(space.UnsafeBakeInputError):
            baker.install_reviewed_narrow_corner_link(
                graph, terrain, obstacles, legacy, contract)

        self.assertEqual(original, graph)

    def test_reviewed_terrain_path_revives_only_listed_edge_cell(self):
        graph = self._reviewed_adapter_graph(width=3, height=2)
        points = ((0.0, 4.0), (4.0, 4.0), (8.0, 4.0))
        graph['heights_mm'][self._adapter_index(graph, points[0])] = 0
        graph['heights_mm'][self._adapter_index(graph, points[2])] = 0
        middle = self._adapter_index(graph, points[1])
        graph['hazards'][middle] = 2
        side = self._adapter_index(graph, (4.0, 0.0))
        graph['hazards'][side] = 2
        contract = {
            'id': 'test_edge_path',
            'kind': 'edge_erosion',
            'points': points,
            'missing_states': {(4.0, 4.0): 2},
            'side_states': {(4.0, 0.0): 2},
            'maximum_water_depth': 0.9,
        }
        terrain, obstacles, legacy = self._reviewed_adapter_dependencies(
            edge=lambda x, unused_z, unused_ground: x != 4.0)

        record = baker.install_reviewed_terrain_path(
            graph, terrain, obstacles, legacy, contract)

        self.assertEqual('edge_erosion', record['kind'])
        self.assertEqual(1, record['revived_nodes'])
        self.assertEqual(4, record['directed_links_added'])
        self.assertEqual(0, graph['heights_mm'][middle])
        self.assertEqual(0, graph['hazards'][middle])
        self.assertIsNone(graph['heights_mm'][side])
        self.assertEqual(2, graph['hazards'][side])
        east = 1 << graph['directions'].index([1, 0])
        west = 1 << graph['directions'].index([-1, 0])
        self.assertTrue(graph['links'][self._adapter_index(
            graph, points[0])] & east)
        self.assertTrue(graph['links'][middle] & east)
        self.assertTrue(graph['links'][middle] & west)
        self.assertTrue(graph['links'][self._adapter_index(
            graph, points[2])] & west)

    def test_reviewed_ford_marks_revived_water_as_shallow_only(self):
        graph = self._reviewed_adapter_graph(width=3, height=2)
        points = ((0.0, 4.0), (4.0, 4.0), (8.0, 4.0))
        graph['heights_mm'][self._adapter_index(graph, points[0])] = 0
        graph['heights_mm'][self._adapter_index(graph, points[2])] = 0
        middle = self._adapter_index(graph, points[1])
        graph['hazards'][middle] = 3
        side = self._adapter_index(graph, (4.0, 0.0))
        graph['hazards'][side] = 1
        contract = {
            'id': 'test_ford',
            'kind': 'ford',
            'points': points,
            'missing_states': {(4.0, 4.0): 3},
            'side_states': {(4.0, 0.0): 1},
            'maximum_water_depth': 1.1,
        }
        terrain, obstacles, legacy = self._reviewed_adapter_dependencies(
            water=lambda x, unused_z, unused_ground:
            1.0 if 2.0 <= x <= 6.0 else 0.0,
            edge=lambda x, unused_z, unused_ground:
            not (2.0 <= x <= 6.0))

        record = baker.install_reviewed_terrain_path(
            graph, terrain, obstacles, legacy, contract)

        self.assertEqual(1.0, record['maximum_water_depth'])
        self.assertEqual(4, graph['hazards'][middle])
        self.assertIsNone(graph['heights_mm'][side])
        self.assertEqual(1, graph['hazards'][side])

    def test_reviewed_terrain_path_rejects_drift_before_mutation(self):
        contract = {
            'id': 'test_edge_path',
            'kind': 'edge_erosion',
            'points': ((0.0, 4.0), (4.0, 4.0), (8.0, 4.0)),
            'missing_states': {(4.0, 4.0): 2},
            'side_states': {(4.0, 0.0): 2},
            'maximum_water_depth': 0.9,
        }
        for name in ('water', 'blocked', 'grade', 'side'):
            with self.subTest(name=name):
                graph = self._reviewed_adapter_graph(width=3, height=2)
                graph['heights_mm'][self._adapter_index(
                    graph, (0.0, 4.0))] = 0
                graph['heights_mm'][self._adapter_index(
                    graph, (8.0, 4.0))] = 0
                graph['hazards'][self._adapter_index(
                    graph, (4.0, 4.0))] = 2
                graph['hazards'][self._adapter_index(
                    graph, (4.0, 0.0))] = (3 if name == 'side' else 2)
                original = copy.deepcopy(graph)
                terrain, obstacles, legacy = self._reviewed_adapter_dependencies(
                    height=(lambda x, unused_z: x)
                    if name == 'grade' else None,
                    water=(lambda unused_x, unused_z, unused_ground: 1.0)
                    if name == 'water' else None,
                    blocked=(lambda unused_x, unused_z, unused_ground,
                             unused_margin: True)
                    if name == 'blocked' else None,
                    edge=lambda unused_x, unused_z, unused_ground: False,
                )

                with self.assertRaises(space.UnsafeBakeInputError):
                    baker.install_reviewed_terrain_path(
                        graph, terrain, obstacles, legacy, contract)

                self.assertEqual(original, graph)

    def test_munchen_underpass_replaces_only_the_centre_bridge_layer(self):
        graph = self._reviewed_adapter_graph(
            origin=(-202.0, 70.0), width=4, height=9)
        path = (
            (-198.0, 74.0), (-194.0, 78.0), (-194.0, 82.0),
            (-194.0, 86.0), (-194.0, 90.0), (-194.0, 94.0),
            (-190.0, 98.0), (-190.0, 102.0),
        )
        for point in (path[0], path[-2], path[-1]):
            graph['heights_mm'][self._adapter_index(graph, point)] = 0
        centre_decks = ((-194.0, 82.0), (-194.0, 86.0),
                        (-194.0, 90.0))
        side_decks = (
            (-198.0, 82.0), (-190.0, 82.0),
            (-198.0, 86.0), (-190.0, 86.0),
            (-198.0, 90.0), (-190.0, 90.0),
        )
        east = 1 << graph['directions'].index([1, 0])
        west = 1 << graph['directions'].index([-1, 0])
        for point in centre_decks:
            graph['heights_mm'][self._adapter_index(graph, point)] = 8500
        for point in side_decks:
            index = self._adapter_index(graph, point)
            graph['heights_mm'][index] = 8500
            graph['links'][index] = east if point[0] == -198.0 else west
        terrain, obstacles, legacy = self._reviewed_adapter_dependencies(
            surface=lambda x, z: 8.5
            if 80.0 <= z <= 92.0 and -198.0 <= x <= -190.0 else None)

        record = baker.install_munchen_underpass(
            graph, terrain, obstacles, legacy)

        self.assertEqual('underpass_layer', record['kind'])
        self.assertEqual(3, record['replaced_upper_layer_nodes'])
        self.assertEqual(2, record['revived_nodes'])
        self.assertEqual(8.5, record['minimum_overhead_clearance'])
        for point in centre_decks + ((-194.0, 78.0), (-194.0, 94.0)):
            self.assertEqual(0, graph['heights_mm'][
                self._adapter_index(graph, point)])
        for point in side_decks:
            index = self._adapter_index(graph, point)
            self.assertEqual(8500, graph['heights_mm'][index])
            self.assertEqual(0, graph['links'][index] &
                             (east if point[0] == -198.0 else west))
        for point in ((-198.0, 78.0), (-190.0, 78.0),
                      (-198.0, 94.0), (-190.0, 94.0)):
            self.assertIsNone(graph['heights_mm'][
                self._adapter_index(graph, point)])

    def test_reviewed_adapter_inventory_stays_map_local(self):
        self.assertEqual(
            {'84_winter', '92_stalingrad'},
            set(baker._REVIEWED_NARROW_CORNER_CONTRACTS))
        self.assertEqual(
            {'29_el_hallouf', '45_north_america',
             '59_asia_great_wall'},
            set(baker._REVIEWED_TERRAIN_PATH_CONTRACTS))
        highway = baker._REVIEWED_TERRAIN_PATH_CONTRACTS[
            '45_north_america']
        self.assertEqual([1.01, 1.25],
                         [item['maximum_water_depth'] for item in highway])

    def test_stock_spawn_ingress_is_downhill_and_one_way(self):
        directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
        graph = {
            'origin': [0.0, 0.0], 'cell_size': 1.0,
            'width': 5, 'height': 1,
            'heights_mm': [None, None, None, None, 0],
            'links': [0, 0, 0, 0, 0],
            'hazards': [2, 2, 2, 2, 0],
        }

        class Terrain(object):
            def height(self, x, unused_z):
                return 4.0 - float(x)

            def water_depth(self, unused_x, unused_z, unused_height):
                return 0.0

        class Obstacles(object):
            def surface_height(self, unused_x, unused_z):
                return None

            def blocked(self, unused_x, unused_z, unused_height, margin):
                return False

        legacy = types.SimpleNamespace(
            DIRECTIONS=directions,
            WATER_DEPTH_LIMIT=0.9,
            VEHICLE_HALF_WIDTH=2.15,
            HAZARD_WATER=1,
            HAZARD_EDGE=2,
            _ground_height=lambda terrain, unused_obstacles, x, z:
                terrain.height(x, z),
            _node_point=lambda value, index: (
                value['origin'][0] + index % value['width'] * value['cell_size'],
                value['origin'][1] + index // value['width'] * value['cell_size']),
        )

        record = baker.find_downhill_spawn_ingress(
            graph, Terrain(), Obstacles(), (0.0, 0.0), (10.0, 0.0), legacy)
        validation = baker.install_downhill_spawn_ingress(graph, record, legacy)

        self.assertEqual([[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]],
                         record['cells'])
        self.assertEqual([4000, 3000, 2000, 1000, 0], graph['heights_mm'])
        self.assertEqual(4, validation['one_way_links'])
        self.assertTrue(validation['forward_connected'])
        self.assertTrue(validation['reverse_links_absent'])
        east = 1 << directions.index((1, 0))
        west = 1 << directions.index((-1, 0))
        self.assertTrue(all(graph['links'][index] & east for index in range(4)))
        self.assertTrue(all(not graph['links'][index] & west for index in range(1, 5)))

    def test_stock_spawn_ingress_rejects_an_uphill_step(self):
        graph = {
            'origin': [0.0, 0.0], 'cell_size': 1.0,
            'width': 3, 'height': 1,
            'heights_mm': [None, None, 0],
            'links': [0, 0, 0], 'hazards': [2, 2, 0],
        }

        class Terrain(object):
            def height(self, x, unused_z):
                return (2.0, 3.0, 0.0)[int(x)]

            def water_depth(self, unused_x, unused_z, unused_height):
                return 0.0

        class Obstacles(object):
            def surface_height(self, unused_x, unused_z):
                return None

            def blocked(self, unused_x, unused_z, unused_height, margin):
                return False

        directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                      (1, 0), (-1, 1), (0, 1), (1, 1))
        legacy = types.SimpleNamespace(
            DIRECTIONS=directions, WATER_DEPTH_LIMIT=0.9,
            VEHICLE_HALF_WIDTH=2.15, HAZARD_WATER=1, HAZARD_EDGE=2,
            _ground_height=lambda terrain, unused_obstacles, x, z:
                terrain.height(x, z),
            _node_point=lambda value, index: (float(index), 0.0),
        )
        with self.assertRaises(space.UnsafeBakeInputError):
            baker.find_downhill_spawn_ingress(
                graph, Terrain(), Obstacles(), (0.0, 0.0), (10.0, 0.0), legacy)


if __name__ == '__main__':
    unittest.main()
