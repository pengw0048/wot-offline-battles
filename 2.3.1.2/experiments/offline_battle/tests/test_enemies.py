import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'
PACKAGE = MODS / 'offline_battle_2312'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entity_setup = _load('offline_battle_entity_setup',
                     PACKAGE / 'entity_setup.py')
for _name in ('gui', 'gui.mods', 'gui.mods.offline_battle_2312'):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules['gui.mods.offline_battle_2312.entity_setup'] = entity_setup
sys.modules['gui.mods.offline_battle_2312.suspension'] = types.ModuleType(
    'gui.mods.offline_battle_2312.suspension')
enemies = _load('offline_battle_enemies', PACKAGE / 'enemies.py')


class FormationTests(unittest.TestCase):
    def test_the_enemies_stand_ahead_of_the_spawn(self):
        places = enemies.formation((0.0, 0.0), 0.0)
        self.assertEqual(len(places), enemies.ENEMY_COUNT)
        for _x, z, _yaw in places:
            self.assertAlmostEqual(z, enemies.ENEMY_RANGE_METRES)

    def test_the_line_is_centred_on_the_heading(self):
        places = enemies.formation((0.0, 0.0), 0.0)
        offsets = sorted(x for x, _z, _yaw in places)
        self.assertAlmostEqual(sum(offsets), 0.0)
        self.assertAlmostEqual(offsets[1] - offsets[0],
                               enemies.ENEMY_SPACING_METRES)

    def test_they_face_the_player(self):
        _x, _z, yaw = enemies.formation((0.0, 0.0), 0.0)[0]
        self.assertAlmostEqual(math.cos(yaw), -1.0)

    def test_the_formation_follows_the_spawn_heading(self):
        places = enemies.formation((0.0, 0.0), math.pi / 2.0)
        for x, _z, _yaw in places:
            self.assertAlmostEqual(x, enemies.ENEMY_RANGE_METRES)

    def test_the_formation_starts_from_the_spawn_point(self):
        places = enemies.formation((100.0, -50.0), 0.0)
        for _x, z, _yaw in places:
            self.assertAlmostEqual(z, -50.0 + enemies.ENEMY_RANGE_METRES)


class PoseTests(unittest.TestCase):
    def test_a_force_remembers_where_it_placed_each_enemy(self):
        force = enemies.EnemyForce(None, 1, lambda message: None)
        force._poses[7] = (10.0, 20.0, 30.0, 1.5)
        self.assertEqual(force.pose(7), (10.0, 20.0, 30.0, 1.5))

    def test_an_unknown_vehicle_has_no_pose(self):
        force = enemies.EnemyForce(None, 1, lambda message: None)
        self.assertIsNone(force.pose(7))


class PropertyTests(unittest.TestCase):
    def test_an_enemy_is_not_the_player_vehicle(self):
        properties = entity_setup.vehicle_properties(
            'cd', 100, 1, 2, 3, team=entity_setup.ENEMY_TEAM,
            is_my_vehicle=False)
        self.assertFalse(properties['isMyVehicle'])
        self.assertEqual(properties['publicInfo']['team'],
                         entity_setup.ENEMY_TEAM)

    def test_each_roster_entry_carries_its_own_session(self):
        entry = entity_setup.roster_entry('id', 'cd', 100, name='Enemy-1',
                                          team=entity_setup.ENEMY_TEAM,
                                          session_id='enemy_0')
        self.assertEqual(entry['avatarSessionID'], 'enemy_0')
        self.assertEqual(entry['team'], entity_setup.ENEMY_TEAM)
        self.assertEqual(set(entry), set(entity_setup.ROSTER_KEYS))

    def test_death_info_matches_the_arena_schema(self):
        info = entity_setup.death_info(5, 7, 2)
        self.assertEqual(set(info), {'victimID', 'killerID', 'equipmentID',
                                     'reasonID', 'numVehiclesAffected'})
        self.assertEqual(info['victimID'], 5)
        self.assertEqual(info['killerID'], 7)


if __name__ == '__main__':
    unittest.main()
