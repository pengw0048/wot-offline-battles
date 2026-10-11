from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.spawn_planner import SpawnPlanner


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
    def test_runtime_uses_one_mapping_for_humans_and_bots_and_resets_next_round(self):
        import types
        from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._spawn_planner = SpawnPlanner(navigation_graph=formation_graph())
        battle._spawn_cache = {(1, 12): ((999, 999, 999), 0)}
        battle._start_message = {
            'players': [{'id': 1, 'team': 1, 'slot': 12, 'vehicle': 'spg'},
                        {'id': 0, 'team': 1, 'slot': 0, 'vehicle': 'spg'}],
            'bots': [{'id': 2, 'team': 1, 'slot': 14}]}
        battle._bot_vehicle_assignments = {(1, 14): 'spg'}
        battle._resolve_descriptor = lambda name: types.SimpleNamespace(
            type=types.SimpleNamespace(tags=('SPG' if name == 'spg' else 'heavyTank',)))
        battle._prepare_spawn_assignments()
        self.assertEqual(battle._spawn_planner.pose(1, 0), battle._formation_pose(1, 12))
        self.assertEqual(battle._spawn_planner.pose(1, 1), battle._formation_pose(1, 14))
        self.assertEqual(battle._spawn_planner.pose(1, 12), battle._formation_pose(1, 0))
        battle._start_message['players'][0]['vehicle'] = 'heavy'
        battle._bot_vehicle_assignments[(1, 14)] = 'heavy'
        battle._prepare_spawn_assignments()
        self.assertEqual(battle._spawn_planner.pose(1, 12), battle._formation_pose(1, 12))

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

    def test_three_artillery_take_rear_slots_on_both_teams(self):
        planner = SpawnPlanner(navigation_graph=formation_graph())
        classes = dict(((team, slot), 'SPG')
                       for team in (1, 2) for slot in (9, 12, 14))
        mapping = planner.assign_artillery_rear(classes)
        for team in (1, 2):
            self.assertEqual([0, 1, 2],
                             [mapping[team, slot] for slot in (9, 12, 14)])
            self.assertEqual(list(range(15)), sorted(
                mapping[team, slot] for slot in range(15)))
            self.assertEqual(4, mapping[team, 4])
            self.assertEqual(9, mapping[team, 0])

    def test_rear_assignment_is_independent_of_slot_number_and_input_order(self):
        graph = formation_graph()
        for points in graph['spawn_formations'].values():
            points.reverse()
        planner = SpawnPlanner(navigation_graph=graph)
        first = planner.assign_artillery_rear({(1, 1): 'SPG', (1, 3): 'SPG'})
        second = planner.assign_artillery_rear({(1, 3): 'SPG', (1, 1): 'SPG'})
        self.assertEqual(first, second)
        self.assertEqual([10, 11], [first[1, slot] for slot in (1, 3)])

    def test_no_artillery_preserves_every_authored_slot(self):
        planner = SpawnPlanner(navigation_graph=formation_graph())
        self.assertEqual(dict(((team, slot), slot)
                              for team in (1, 2) for slot in range(15)),
                         planner.assign_artillery_rear({}))


if __name__ == '__main__':
    unittest.main()
