import importlib.util
import hashlib
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/prebaked_navigation.py"
)


def load_navigation_loader(mod_directory):
    module_names = (
        "gui", "gui.mods", "gui.mods.offhangar",
        "gui.mods.offhangar.paths",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    gui = types.ModuleType("gui")
    mods = types.ModuleType("gui.mods")
    offhangar = types.ModuleType("gui.mods.offhangar")
    paths = types.ModuleType("gui.mods.offhangar.paths")
    paths.mod_dir = lambda: str(mod_directory)
    sys.modules.update({
        "gui": gui,
        "gui.mods": mods,
        "gui.mods.offhangar": offhangar,
        "gui.mods.offhangar.paths": paths,
    })
    try:
        spec = importlib.util.spec_from_file_location(
            "prebaked_navigation_under_test", LOADER_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


class PrebakedNavigationLoaderTest(unittest.TestCase):
    def graph(self):
        return {
            "format": "offhangar-navgraph",
            "version": 1,
            "game_version": "0.8.2",
            "map": "07_lakeville",
            "cell_size": 4.0,
            "origin": [0.0, 0.0],
            "width": 2,
            "height": 1,
            "heights_mm": [0, 0],
            "links": [16, 8],
            "spawn_formations": {
                "1": [[0.0, 0.0, 0.0, 0.0]] * 15,
                "2": [[4.0, 0.0, 0.0, 0.0]] * 15,
            },
            "validation": {
                "spawn_compiled_bsp_obb_clearance": True,
                "spawn_pairwise_obb_clearance": True,
                "spawn_terrain_footprint_clearance": True,
                "route_terminal_obb_clearance": True,
            },
            "bake": {
                "spawn_obstacle_skipped_models": 0,
                "spawn_obstacle_conservative_fallback_models": 0,
            },
        }

    def test_loads_only_a_matching_versioned_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            navgraphs = Path(directory) / "navgraphs"
            navgraphs.mkdir()
            graph_path = navgraphs / "07_lakeville.json"
            graph_path.write_text(json.dumps(self.graph()))
            loader = load_navigation_loader(directory)

            graph = loader.load_graph("spaces/07_lakeville")
            self.assertEqual("07_lakeville", graph["map"])
            self.assertIsNone(loader.load_graph("04_himmelsdorf"))

            invalid = self.graph()
            invalid["game_version"] = "0.9.22"
            graph_path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                loader.load_graph("07_lakeville")

    def test_complete_manifest_loads_the_named_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            navgraphs = Path(directory) / "navgraphs"
            navgraphs.mkdir()
            graph_path = navgraphs / "07_lakeville.json"
            payload = json.dumps(self.graph(), sort_keys=True).encode("utf-8")
            graph_path.write_bytes(payload)
            manifest = {
                "format": "offhangar-navgraph-manifest",
                "version": 1,
                "game_version": "0.8.2",
                "maps": [{
                    "map": "07_lakeville",
                    "file": "07_lakeville.json",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }
            (navgraphs / "manifest.json").write_text(json.dumps(manifest))
            loader = load_navigation_loader(directory)
            loader.STOCK_MAPS = ("07_lakeville",)

            self.assertEqual(
                "07_lakeville", loader.load_graph("07_lakeville")["map"]
            )
            # A different line ending is still the same graph.
            graph_path.write_bytes(payload + b"\n")
            self.assertEqual(
                "07_lakeville", loader.load_graph("07_lakeville")["map"])

    def test_nearest_ground_point_uses_safe_baked_height(self):
        loader = load_navigation_loader("/unused")
        graph = self.graph()
        graph["heights_mm"] = [1250, -3200]
        graph["hazards"] = [0, 1]

        self.assertEqual(
            (0.0, 1.25, 0.0),
            loader.nearest_ground_point(graph, 3.9, 0.0, 1),
        )

    def test_spawn_pose_returns_only_a_complete_finite_slot(self):
        loader = load_navigation_loader("/unused")
        graph = self.graph()
        graph["spawn_formations"] = {
            "1": [[1.0, 2.0, 3.0, 0.25]] * 15,
            "2": [[4.0, 5.0, 6.0, -0.25]] * 15,
        }

        self.assertEqual((1.0, 2.0, 3.0, 0.25),
                         loader.spawn_pose(graph, 1, 10))
        self.assertIsNone(loader.spawn_pose(graph, 1, 15))
        graph["spawn_formations"]["1"][10] = [1.0, float("nan"), 3.0, 0.0]
        self.assertIsNone(loader.spawn_pose(graph, 1, 10))

    def test_stock_graph_validation_requires_complete_spawn_formations(self):
        loader = load_navigation_loader("/unused")
        loader.STOCK_MAPS = ("test_map",)
        graph = self.graph()
        graph["map"] = "test_map"
        graph.pop("spawn_formations")

        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")

        graph["spawn_formations"] = {
            "1": [[1.0, 2.0, 3.0, 0.0]] * 15,
            "2": [[4.0, 5.0, 6.0, 0.0]] * 15,
        }
        graph["validation"] = {
            "spawn_compiled_bsp_obb_clearance": True,
            "spawn_pairwise_obb_clearance": True,
            "spawn_terrain_footprint_clearance": True,
            "route_terminal_obb_clearance": True,
        }
        self.assertIs(graph, loader._validate(graph, "test_map"))

        graph["validation"].pop("route_terminal_obb_clearance")
        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")
        graph["validation"]["route_terminal_obb_clearance"] = False
        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")
        graph["validation"]["route_terminal_obb_clearance"] = True

        graph["validation"].pop("spawn_terrain_footprint_clearance")
        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")
        graph["validation"]["spawn_terrain_footprint_clearance"] = False
        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")
        graph["validation"]["spawn_terrain_footprint_clearance"] = True

        graph["bake"].pop("spawn_obstacle_skipped_models")
        with self.assertRaisesRegex(ValueError, "validated spawn poses"):
            loader._validate(graph, "test_map")
        for invalid in (1, -1, 0.0, "0", False, None):
            graph["bake"]["spawn_obstacle_skipped_models"] = invalid
            with self.assertRaisesRegex(ValueError, "validated spawn poses"):
                loader._validate(graph, "test_map")
        graph["bake"]["spawn_obstacle_skipped_models"] = 0

        graph["spawn_formations"]["2"][14] = [1.0, float("nan"), 3.0, 0.0]
        with self.assertRaisesRegex(ValueError, "spawn pose is invalid"):
            loader._validate(graph, "test_map")

    def test_shipped_bundle_contains_every_validated_stock_map(self):
        mod_directory = ROOT / "scripts/client/gui/mods/offhangar"
        loader = load_navigation_loader(mod_directory)
        manifest = json.loads(
            (mod_directory / "navgraphs/manifest.json").read_text()
        )
        self.assertEqual(set(loader.STOCK_MAPS), {
            record["map"] for record in manifest["maps"]
        })

        loaded = {}
        for map_name in loader.STOCK_MAPS:
            graph = loader.load_graph(map_name)
            loaded[map_name] = graph
            self.assertEqual(1, graph["validation"]["components"], map_name)
            self.assertEqual(1.0, graph["validation"]["largest_fraction"], map_name)

            self.assertEqual({"1", "2"}, set(graph["spawn_formations"]), map_name)
            self.assertEqual(15, len(graph["spawn_formations"]["1"]), map_name)
            self.assertEqual(15, len(graph["spawn_formations"]["2"]), map_name)
            self.assertTrue(
                graph["validation"]["spawn_compiled_bsp_obb_clearance"], map_name)
            self.assertTrue(
                graph["validation"]["spawn_pairwise_obb_clearance"], map_name)
            self.assertTrue(
                graph["validation"]["route_terminal_obb_clearance"], map_name)
            self.assertTrue(
                graph["validation"].get("spawn_terrain_footprint_clearance"),
                map_name)
            self.assertEqual(
                0, graph["bake"]["spawn_obstacle_skipped_models"], map_name)
            self.assertGreaterEqual(
                graph["bake"]["spawn_obstacle_conservative_fallback_models"],
                0, map_name)
            for team in ("1", "2"):
                for slot, pose in enumerate(graph["spawn_formations"][team]):
                    self.assertEqual(4, len(pose), (map_name, team, slot))
                    self.assertTrue(all(math.isfinite(float(value))
                                        for value in pose),
                                    (map_name, team, slot, pose))
                    cell_x = int(round(
                        (float(pose[0]) - graph["origin"][0]) /
                        graph["cell_size"]
                    ))
                    cell_z = int(round(
                        (float(pose[2]) - graph["origin"][1]) /
                        graph["cell_size"]
                    ))
                    index = cell_z * graph["width"] + cell_x
                    self.assertIsNotNone(
                        graph["heights_mm"][index], (map_name, team, slot))
                    self.assertEqual(
                        0, int(graph["hazards"][index]), (map_name, team, slot))
                    self.assertGreaterEqual(
                        bin(int(graph["links"][index])).count("1"), 3,
                        (map_name, team, slot))
                    self.assertAlmostEqual(
                        float(graph["heights_mm"][index]) / 1000.0,
                        float(pose[1]), places=6)
            for team in ("1", "2"):
                self.assertTrue(graph["routes"][team], (map_name, team))
                for route in graph["routes"][team]:
                    self.assertLessEqual(len(route["waypoints"]), 16)
                    for x, z, unused_hold in route["waypoints"]:
                        cell_x = int(round(
                            (x - graph["origin"][0]) / graph["cell_size"]
                        ))
                        cell_z = int(round(
                            (z - graph["origin"][1]) / graph["cell_size"]
                        ))
                        index = cell_z * graph["width"] + cell_x
                        self.assertIsNotNone(
                            graph["heights_mm"][index],
                            (map_name, team, route["id"], x, z),
                        )

        corrected_ctf_bases = {
            "01_karelia": ((397.6, 402.6), (-401.3, -399.9)),
            "04_himmelsdorf": ((-47.5, -302.6), (17.1, 300.0)),
            "15_komarin": ((-280.772, -192.392), (282.752, 167.894)),
            "23_westfeld": ((-300.1, -339.6), (339.4, 299.8)),
            "28_desert": ((373.4855, -178.9612), (-405.0387, 137.5266)),
            "29_el_hallouf": ((299.256, 319.406), (-338.5832, -319.3074)),
        }
        for map_name, expected in corrected_ctf_bases.items():
            for actual_base, expected_base in zip(
                    loaded[map_name]["bases"], expected):
                self.assertAlmostEqual(expected_base[0], actual_base[0], places=3)
                self.assertAlmostEqual(expected_base[1], actual_base[1], places=3)

        komarin = loaded["15_komarin"]
        self.assertGreaterEqual(komarin["bake"]["bridge_surface_triangles"], 200)
        self.assertLess(komarin["bake"]["pruned_nodes"], 100)
        self.assertLess(komarin["validation"]["base_path_metres"], 1100.0)

        himmelsdorf = loaded["04_himmelsdorf"]
        routes = {route["id"]: route for route in himmelsdorf["routes"]["1"]}
        self.assertEqual({"rail", "banana", "hill", "rear_guard"}, set(routes))

        def route_heights(route):
            result = []
            for x, z, unused_hold in route["waypoints"]:
                cell_x = int(round(
                    (x - himmelsdorf["origin"][0]) / himmelsdorf["cell_size"]
                ))
                cell_z = int(round(
                    (z - himmelsdorf["origin"][1]) / himmelsdorf["cell_size"]
                ))
                index = cell_z * himmelsdorf["width"] + cell_x
                result.append(himmelsdorf["heights_mm"][index] / 1000.0)
            return result

        self.assertLess(min(point[0] for point in routes["rail"]["waypoints"]), -200)
        self.assertLess(max(route_heights(routes["banana"])), 10.0)
        self.assertGreater(max(route_heights(routes["hill"])), 40.0)


if __name__ == "__main__":
    unittest.main()
