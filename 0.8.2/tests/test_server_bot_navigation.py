import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import server_bot_navigation
from server_bot_navigation import BotPathResolver


ROOT = Path(__file__).resolve().parents[1]


class ServerBotNavigationTests(unittest.TestCase):
    def setUp(self):
        with (ROOT / "scripts/client/gui/mods/offhangar/navgraphs/07_lakeville.json").open() as source:
            self.graph = json.load(source)
        self.frame = self._frame(self.graph)
        self.resolver = BotPathResolver()
        self.assertTrue(self.resolver.configure("07_lakeville", self.frame))

    @staticmethod
    def _frame(graph):
        first, second = graph["bases"][:2]
        return {
            "origin": [first[0], first[1]],
            "axis": [second[0] - first[0], second[1] - first[1]],
        }

    def _shared(self, world):
        return self.resolver._shared((float(world[0]), 0.0, float(world[1])))

    @staticmethod
    def _dict(point):
        return {"x": point[0], "y": point[1], "z": point[2]}

    def _resolve_route(self, order, state, revision=1, start=1.0):
        for tick in range(600):
            resolved = self.resolver.resolve(
                [order], [state], revision, start + tick / 30.0)[order["id"]]
            if resolved["nav_source"] != "server_hold":
                return resolved
            if self.resolver.diagnostics()["pending"] == 0:
                return resolved
        self.fail("server navigation search did not finish")

    def test_coordinate_frame_round_trips_graph_world_points(self):
        world = (-322.0, 17.5, 62.0)
        shared = self.resolver._shared(world)
        restored = self.resolver._world(shared)

        self.assertAlmostEqual(world[0], restored[0], places=5)
        self.assertAlmostEqual(world[1], restored[1], places=5)
        self.assertAlmostEqual(world[2], restored[2], places=5)

    def test_route_order_gets_a_safe_short_server_waypoint(self):
        route = self.graph["routes"]["1"][0]
        start = self._shared(route["waypoints"][0])
        goal = self._shared(route["waypoints"][5])
        order = {
            "id": 1, "team": 1, "combat_mode": "route",
            "route_id": route["id"], "route_index": 5,
            "route_anchor": self._dict(start),
            "move_position": self._dict(goal),
            "target_id": None, "throttle_override": None,
        }
        state = dict(self._dict(start), id=1, team=1, alive=True)

        resolved = self._resolve_route(order, state, 7)
        target = self.resolver._world((resolved["nav_x"], resolved["nav_y"],
                                       resolved["nav_z"]))
        current = self.resolver._world(start)

        self.assertEqual("server_baked", resolved["nav_source"])
        self.assertEqual(7, resolved["nav_order_revision"])
        self.assertGreater(((target[0] - current[0]) ** 2 +
                            (target[2] - current[2]) ** 2) ** 0.5, 1.0)
        self.assertTrue(self.resolver.grid.segment_clear(current, target))

    def test_hold_order_does_not_invoke_astar(self):
        start = self._shared(self.graph["bases"][0])
        goal = (start[0] + 50.0, start[1], start[2] + 50.0)
        order = {
            "id": 1, "team": 1, "combat_mode": "engage",
            "move_position": self._dict(goal), "throttle_override": 0.0,
        }
        state = dict(self._dict(start), id=1, team=1, alive=True)

        resolved = self.resolver.resolve([order], [state], 3, 1.0)[1]

        self.assertEqual("server_hold", resolved["nav_source"])
        self.assertEqual(0, self.resolver.diagnostics()["plans"])

    def test_near_goal_raw_target_requires_clear_hazard_free_segment(self):
        route = next(item for item in self.graph["routes"]["1"]
                     if item["id"] == "lake_road")
        start_world, goal_world = route["waypoints"][:2]

        for segment_clear, has_hazard, returns_raw_goal in (
                (True, False, True),
                (False, False, False),
                (True, True, False)):
            with self.subTest(segment_clear=segment_clear,
                              has_hazard=has_hazard):
                resolver = BotPathResolver()
                self.assertTrue(resolver.configure("07_lakeville", self.frame))
                start = resolver._shared(
                    (float(start_world[0]), 0.0, float(start_world[1])))
                goal = resolver._shared(
                    (float(goal_world[0]), 0.0, float(goal_world[1])))
                self.assertLessEqual(
                    math.hypot(goal_world[0] - start_world[0],
                               goal_world[1] - start_world[1]),
                    15.0,
                )
                order = {
                    "id": 1, "team": 1, "combat_mode": "route",
                    "route_id": route["id"], "route_index": 1,
                    "route_anchor": self._dict(start),
                    "move_position": self._dict(goal),
                    "target_id": None, "throttle_override": None,
                }
                state = dict(self._dict(start), id=1, team=1, alive=True)

                with mock.patch.object(
                        resolver.grid, "segment_clear",
                        return_value=segment_clear) as clear_probe, \
                        mock.patch.object(
                            resolver.grid, "segment_has_baked_hazard",
                            return_value=has_hazard) as hazard_probe, \
                        mock.patch.object(
                            resolver.grid, "begin_plan",
                            wraps=resolver.grid.begin_plan) as begin_plan:
                    target, source = resolver._resolve_one(
                        order, state, 4, 1.0)

                self.assertEqual("server_hold", source)
                clear_probe.assert_called()
                if returns_raw_goal:
                    self.assertEqual(goal, target)
                    hazard_probe.assert_called_once()
                    begin_plan.assert_not_called()
                else:
                    self.assertEqual(start, target)
                    self.assertNotEqual(goal, target)
                    begin_plan.assert_called_once()

    def test_base_defense_path_identity_ignores_combat_target_changes(self):
        base = self._shared(self.graph["bases"][0])
        start = (base[0], base[1], base[2] + 80.0)
        goal = base
        first_order = {
            "id": 11,
            "team": 1,
            "combat_mode": "base_defense",
            "defense_base_id": "1:0",
            "target_kind": "human",
            "target_id": 7,
            "move_position": self._dict(goal),
            "throttle_override": None,
        }
        second_order = dict(first_order, target_id=8)
        first_identity = self.resolver._path_identity(
            first_order, self.resolver._world(goal)
        )
        second_identity = self.resolver._path_identity(
            second_order, self.resolver._world(goal)
        )

        self.assertEqual(first_identity, second_identity)
        self.assertEqual(
            ("bot", 11, "base_defense", "1:0"),
            first_identity[:-1],
        )

    def test_missing_graph_fails_closed(self):
        resolver = BotPathResolver()
        with self.assertRaises(OSError):
            resolver.configure("not_a_map", self.frame)
        self.assertFalse(resolver.active)

    def test_missing_authority_frame_fails_closed(self):
        resolver = BotPathResolver()
        with self.assertRaises(ValueError):
            resolver.configure("07_lakeville")
        self.assertFalse(resolver.active)

    def test_non_finite_authority_frame_fails_closed(self):
        resolver = BotPathResolver()
        with self.assertRaises(ValueError):
            resolver.configure("07_lakeville", {
                "origin": [float("nan"), 0.0], "axis": [0.0, 1.0],
            })
        self.assertFalse(resolver.active)

    def test_a_rewritten_graph_still_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            source = ROOT / "scripts/client/gui/mods/offhangar/navgraphs"
            for source_path in source.glob("*.json"):
                shutil.copy2(source_path, Path(directory) / source_path.name)
            target = Path(directory) / "07_lakeville.json"
            data = target.read_bytes()
            target.write_bytes(data + b"\n")
            with mock.patch.object(
                    server_bot_navigation, "_graph_directories",
                    return_value=(directory,)):
                resolver = BotPathResolver()
                self.assertTrue(
                    resolver.configure("07_lakeville", self.frame))

    def test_manifest_checksum_accepts_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = ROOT / "scripts/client/gui/mods/offhangar/navgraphs"
            for source_path in source.glob("*.json"):
                shutil.copy2(source_path, Path(directory) / source_path.name)
            target = Path(directory) / "07_lakeville.json"
            data = target.read_bytes()
            self.assertTrue(data.endswith(b"\n"))
            target.write_bytes(data[:-1] + b"\r\n")
            with mock.patch.object(
                    server_bot_navigation, "_graph_directories",
                    return_value=(directory,)):
                resolver = BotPathResolver()
                resolver.configure("07_lakeville", self.frame)
                self.assertTrue(resolver.active)

    def test_stock_graph_requires_route_terminal_obb_clearance_proof(self):
        for proof in (None, False):
            with self.subTest(proof=proof):
                graph = json.loads(json.dumps(self.graph))
                if proof is None:
                    graph["validation"].pop(
                        "route_terminal_obb_clearance", None)
                else:
                    graph["validation"][
                        "route_terminal_obb_clearance"] = proof
                with self.assertRaisesRegex(
                        ValueError, "route terminal.*clearance"):
                    server_bot_navigation._validate_graph(
                        graph, "07_lakeville")

        graph = json.loads(json.dumps(self.graph))
        graph["validation"]["route_terminal_obb_clearance"] = True
        self.assertIs(
            graph,
            server_bot_navigation._validate_graph(graph, "07_lakeville"),
        )

    def test_inactive_resolver_explicitly_requests_client_fallback(self):
        resolver = BotPathResolver()
        state = {"id": 9, "team": 1, "alive": True,
                 "x": 0.0, "y": 0.0, "z": 0.0}

        resolved = resolver.resolve([], [state], 5, 1.0)

        self.assertEqual("client_fallback", resolved[9]["nav_source"])
        self.assertEqual(5, resolved[9]["nav_order_revision"])

    def test_missing_move_position_requests_client_fallback(self):
        start = self._shared(self.graph["bases"][0])
        order = {"id": 1, "team": 1, "combat_mode": "route"}
        state = dict(self._dict(start), id=1, team=1, alive=True)

        resolved = self.resolver.resolve([order], [state], 3, 1.0)[1]

        self.assertEqual("client_fallback", resolved["nav_source"])
        self.assertEqual(0, self.resolver.diagnostics()["plans"])

    def test_missing_order_explicitly_clears_stale_server_waypoint(self):
        start = self._shared(self.graph["bases"][0])
        state = dict(self._dict(start), id=7, team=1, alive=True)

        resolved = self.resolver.resolve([], [state], 6, 1.0)

        self.assertEqual("client_fallback", resolved[7]["nav_source"])
        self.assertEqual(6, resolved[7]["nav_order_revision"])

    def test_identical_route_reuses_cached_path(self):
        route = self.graph["routes"]["1"][0]
        start = self._shared(route["waypoints"][0])
        goal = self._shared(route["waypoints"][7])
        order = {
            "id": 1, "team": 1, "combat_mode": "route",
            "route_id": route["id"], "route_index": 7,
            "route_anchor": self._dict(start),
            "move_position": self._dict(goal),
            "target_id": None, "throttle_override": None,
        }
        state = dict(self._dict(start), id=1, team=1, alive=True)

        self._resolve_route(order, state, 4, 1.0)
        first = self.resolver.diagnostics()
        self.resolver._bot_paths.clear()
        self._resolve_route(order, state, 4, 1.1)
        second = self.resolver.diagnostics()

        self.assertEqual(first["plans"], second["plans"])
        self.assertGreater(second["cache_hits"], first["cache_hits"])

    def test_negative_cache_expires_even_when_requested_every_tick(self):
        resolver = self.resolver
        start = resolver._world(self._shared(self.graph["bases"][0]))
        goal = (start[0] + 40.0, start[1], start[2] + 40.0)
        key = ("negative-cache",)
        resolver._path_cache[key] = ()
        resolver._path_times[key] = 1.0
        resolver._path_complete[key] = False

        with mock.patch.object(
                resolver.grid, "segment_clear", return_value=False), mock.patch.object(
                resolver.grid, "begin_plan",
                wraps=resolver.grid.begin_plan) as begin:
            for tick in range(1, 20):
                resolver._request_path(
                    key, start, goal, 1.0 + tick / 30.0)

        self.assertEqual(1, begin.call_count)

    def test_moving_combat_bot_keeps_path_when_tactical_target_is_unchanged(self):
        route = self.graph["routes"]["1"][0]
        start_world = route["waypoints"][0]
        next_world = route["waypoints"][1]
        goal_world = route["waypoints"][7]
        start = self._shared(start_world)
        moved = self._shared(next_world)
        goal = self._shared(goal_world)
        self.assertNotEqual(
            self.resolver.grid.cell_for(self.resolver._world(start)),
            self.resolver.grid.cell_for(self.resolver._world(moved)),
        )
        order = {
            "id": 1, "team": 1, "combat_mode": "pursue",
            "target_kind": "bot", "target_id": 19,
            "move_position": self._dict(goal), "throttle_override": None,
        }
        first_state = dict(self._dict(start), id=1, team=1, alive=True)
        moved_state = dict(self._dict(moved), id=1, team=1, alive=True)

        self._resolve_route(order, first_state, 4, 1.0)
        first = self.resolver.diagnostics()
        self._resolve_route(order, moved_state, 4, 1.1)
        second = self.resolver.diagnostics()

        self.assertEqual(first["plans"], second["plans"])

    def test_cold_astar_is_queued_instead_of_blocking_resolve(self):
        route = self.graph["routes"]["1"][0]
        start = self._shared(route["waypoints"][0])
        goal = self._shared(route["waypoints"][-1])
        order = {
            "id": 1, "team": 1, "combat_mode": "pursue",
            "target_kind": "bot", "target_id": 19,
            "move_position": self._dict(goal), "throttle_override": None,
        }
        state = dict(self._dict(start), id=1, team=1, alive=True)

        resolved = self.resolver.resolve([order], [state], 4, 1.0)[1]

        self.assertEqual("server_hold", resolved["nav_source"])
        self.assertEqual(1, self.resolver.diagnostics()["pending"])
        self.assertEqual(0, self.resolver.diagnostics()["search_steps"])

    def test_partial_paths_continue_until_the_real_goal(self):
        graph_path = ROOT / "scripts/client/gui/mods/offhangar/navgraphs/51_asia.json"
        with graph_path.open() as source:
            graph = json.load(source)
        resolver = BotPathResolver()
        resolver.configure("51_asia", self._frame(graph))
        route = next(item for item in graph["routes"]["2"]
                     if item["id"] == "west_terraces")
        start_world = route["waypoints"][1]
        goal_world = route["waypoints"][2]
        start = resolver._shared((start_world[0], 0.0, start_world[1]))
        goal = resolver._shared((goal_world[0], 0.0, goal_world[1]))
        order = {
            "id": 1, "team": 2, "combat_mode": "route",
            "route_id": route["id"], "route_index": 2,
            "route_anchor": self._dict(start),
            "move_position": self._dict(goal),
            "target_id": None, "throttle_override": None,
        }
        state = dict(self._dict(start), id=1, team=2, alive=True)
        reached = False

        # The structure-aware graph has a real local minimum on this leg.  A
        # 2,048-node cap still forces one partial result from the original
        # anchor, while leaving enough bounded work for the continuation from
        # that safe endpoint to get around the ridge.
        with mock.patch.object(server_bot_navigation, "MAX_EXPANSIONS", 2048):
            for tick in range(2400):
                resolved = resolver.resolve(
                    [order], [state], 8, 1.0 + tick / 30.0)[1]
                if resolved["nav_source"] in ("server_baked", "server_hold"):
                    for key in ("x", "y", "z"):
                        state[key] = resolved.get("nav_" + key, state[key])
                world = resolver._world((state["x"], state["y"], state["z"]))
                if math.hypot(world[0] - goal_world[0],
                              world[2] - goal_world[1]) <= 13.0:
                    reached = True
                    break

        self.assertTrue(reached)
        self.assertGreater(resolver.diagnostics()["partials"], 0)
        self.assertLess(resolver.diagnostics()["max_budget_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
