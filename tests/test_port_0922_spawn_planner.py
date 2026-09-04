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


if __name__ == '__main__':
    unittest.main()
