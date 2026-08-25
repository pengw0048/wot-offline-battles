import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.spawn_planner import (
    MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO, SpawnPlanner)
from gui.mods.offline_lan_0922.ai import maps as tactical_maps


def formation_graph():
    team_one = []
    team_two = []
    for slot in range(15):
        row, column = divmod(slot, 5)
        team_one.append((column * 12.0, 3.0, -400.0 + row * 14.0, 0.0))
        team_two.append((column * 12.0, 4.0, 400.0 - row * 14.0, 3.14159))
    return {
        'map': '06_ensk',
        'objective_bases': ((12.5, -390.25), (18.75, 389.5)),
        'spawn_formations': {'1': team_one, '2': team_two},
    }


class SpawnPlannerTests(unittest.TestCase):
    def test_exact_baked_slot_and_height_are_returned_unchanged(self):
        graph = formation_graph()
        planner = SpawnPlanner(navigation_graph=graph)

        self.assertEqual(((24.0, 3.0, -386.0), 0.0),
                         planner.pose(1, 7))
        self.assertEqual(((48.0, 4.0, 372.0), 3.14159),
                         planner.pose(2, 14))
        self.assertEqual(
            {1: ((12.5, -390.25),), 2: ((18.75, 389.5),)},
            planner.bases)

    def test_runtime_refuses_to_invent_a_formation(self):
        with self.assertRaisesRegex(
                ValueError, 'validated navigation graph is required'):
            SpawnPlanner()
        with self.assertRaisesRegex(ValueError, 'spawn formations are missing'):
            SpawnPlanner(navigation_graph={'map': '06_ensk'})

        graph = formation_graph()
        del graph['objective_bases']
        with self.assertRaisesRegex(ValueError, 'objective bases are missing'):
            SpawnPlanner(navigation_graph=graph)

    def test_every_team_must_have_exactly_fifteen_slots(self):
        graph = formation_graph()
        graph['spawn_formations']['2'] = graph['spawn_formations']['2'][:-1]

        with self.assertRaisesRegex(ValueError, 'exactly 15 spawn slots'):
            SpawnPlanner(navigation_graph=graph)

    def test_overlapping_slots_are_rejected_with_coordinates(self):
        graph = formation_graph()
        graph['spawn_formations']['1'][1] = graph['spawn_formations']['1'][0]

        with self.assertRaisesRegex(ValueError, 'spawn overlap: team 1 slot 1'):
            SpawnPlanner(navigation_graph=graph)

    def test_unknown_team_and_slot_fail_instead_of_clamping(self):
        planner = SpawnPlanner(navigation_graph=formation_graph())

        with self.assertRaisesRegex(ValueError, 'no spawn team 3'):
            planner.pose(3, 0)
        with self.assertRaisesRegex(ValueError, 'no spawn slot 15'):
            planner.pose(1, 15)

    def test_karelia_maps_server_teams_to_stock_graph_teams(self):
        path = ROOT / '0.9.22' / 'navgraphs' / '01_karelia.json'
        with path.open('r', encoding='utf-8') as handle:
            graph = json.load(handle)
        planner = SpawnPlanner(
            fallback=tactical_maps.get_tactical_map('01_karelia'),
            navigation_graph=graph)

        self.assertEqual({1: 2, 2: 1},
                         planner.graph_team_by_server_team)
        self.assertEqual(tuple(graph['spawn_formations']['2'][0]),
                         planner.pose(1, 0)[0] +
                         (planner.pose(1, 0)[1],))
        self.assertEqual(
            tuple(graph['objective_bases'][1]), planner.bases[1][0])

    def test_all_shipped_maps_have_a_unique_server_team_mapping(self):
        directory = ROOT / '0.9.22' / 'navgraphs'
        with (directory / 'manifest.json').open(
                'r', encoding='utf-8') as handle:
            manifest = json.load(handle)
        loaded = []
        worst_ratio = 0.0
        minimum_margin = float('inf')
        for record in manifest['maps']:
            with (directory / record['file']).open(
                    'r', encoding='utf-8') as handle:
                graph = json.load(handle)
            map_name = record['map']
            planner = SpawnPlanner(
                fallback=tactical_maps.get_tactical_map(map_name),
                navigation_graph=graph)
            loaded.append(map_name)
            self.assertEqual({1, 2},
                             set(planner.graph_team_by_server_team))
            self.assertEqual({1, 2}, set(
                planner.graph_team_by_server_team.values()))
            bounds = graph['bounds']
            diagonal = ((bounds[2] - bounds[0]) ** 2 +
                        (bounds[3] - bounds[1]) ** 2) ** 0.5
            homes = tactical_maps.get_tactical_map(map_name)['bases']
            scores = []
            for mapping in ((1, 2), (2, 1)):
                scores.append(sum(
                    ((homes[server_team][0] -
                      graph['objective_bases'][mapping[server_team - 1] - 1][0]) ** 2 +
                     (homes[server_team][1] -
                      graph['objective_bases'][mapping[server_team - 1] - 1][1]) ** 2) ** 0.5
                    for server_team in (1, 2)))
            minimum_margin = min(minimum_margin, abs(scores[0] - scores[1]))
            for server_team in (1, 2):
                graph_index = planner.graph_team_by_server_team[server_team] - 1
                objective = graph['objective_bases'][graph_index]
                anchor = graph['spawn_anchors'][graph_index]
                distances = (
                    ((homes[server_team][0] - objective[0]) ** 2 +
                     (homes[server_team][1] - objective[1]) ** 2) ** 0.5,
                    ((homes[server_team][0] - anchor[0]) ** 2 +
                     (homes[server_team][1] - anchor[1]) ** 2) ** 0.5,
                    ((objective[0] - anchor[0]) ** 2 +
                     (objective[1] - anchor[1]) ** 2) ** 0.5,
                )
                worst_ratio = max(
                    worst_ratio, max(distances) / diagonal)
        self.assertEqual(41, len(loaded))
        self.assertLessEqual(
            worst_ratio, MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO)
        self.assertGreater(minimum_margin, 1.0)


if __name__ == '__main__':
    unittest.main()
