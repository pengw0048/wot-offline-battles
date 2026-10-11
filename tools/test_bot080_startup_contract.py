from __future__ import print_function
"""Roster integration against production source or the shipped Python 2.7 pyc.

No planner, vehicle-eligibility function, or assignment method is replaced.
The minimal catalog is a test double for the client-owned vehicle database.
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP = None
if len(sys.argv) == 3 and sys.argv[1] == '--compiled':
    TEMP = tempfile.mkdtemp(prefix='bot080-startup-pyc-')
    with zipfile.ZipFile(sys.argv[2]) as archive:
        archive.extractall(TEMP)
    CLIENT = os.path.join(TEMP, 'res', 'scripts', 'client')
    del sys.argv[1:]
else:
    CLIENT = os.path.join(ROOT, 'src', 'res', 'scripts', 'client')
sys.path.insert(0, CLIENT)
for package, subpath in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
    module = types.ModuleType(package)
    module.__path__ = [os.path.join(CLIENT, subpath)]
    sys.modules[package] = module
sys.modules['gui'].mods = sys.modules['gui.mods']
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.ai import planner


class Record(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def make_battle(worker, mode):
    candidates = [
        Record(name='germany:G119_Pz58_Mutz', level=8, tags=('mediumTank',)),
        Record(name='germany:G04_PzVI_Tiger_I', level=7, tags=('heavyTank',)),
        Record(name='germany:G16_PzVIB_Tiger_II', level=8, tags=('heavyTank',)),
        Record(name='germany:G03_PzV_Panther', level=7, tags=('mediumTank',)),
        Record(name='germany:G44_JagdTiger', level=9, tags=('AT-SPG',)),
        Record(name='germany:G34_E_100', level=10, tags=('heavyTank',)),
    ]
    by_name = dict((row.name, Record(type=row)) for row in candidates)
    player_name = candidates[0].name
    battle = BattleRuntime.__new__(BattleRuntime)
    battle._runtime = Record(
        nations=Record(AVAILABLE_NAMES=('germany',), INDICES={'germany': 0}),
        vehicles=Record(g_list=Record(
            getList=lambda unused: dict(enumerate(candidates)))))
    battle.client = Record(player_id=-1 if worker else 1, team=2)
    battle._worker_mode = worker
    battle._config = {'vehicle': player_name}
    battle._resolve_descriptor = lambda name: by_name[name]
    battle._bot_vehicle_assignments = {}
    battle._start_message = {
        'round_id': 1, 'map': '31_airfield', 'bot_tier_mode': mode,
        'players': [{'id': 1, 'team': 2, 'slot': 0, 'vehicle': player_name}],
        'bots': [{'id': 100 + team * 20 + slot, 'team': team, 'slot': slot}
                 for team in (1, 2) for slot in range(15)
                 if not (team == 2 and slot == 0)],
        'bot_lineup': [{'team': 1, 'slot': 0, 'vehicle': candidates[2].name},
                      {'team': 2, 'slot': 1, 'skill': 'normal'}],
    }
    return battle, by_name[player_name]


class RosterContractTests(unittest.TestCase):
    def test_all_presets_build_29_bots_identically_for_worker_and_player(self):
        for mode in planner.BOT_TIER_MODES:
            worker, descriptor = make_battle(True, mode)
            player, player_descriptor = make_battle(False, mode)
            self.assertTrue(worker._prepare_bot_vehicle_assignments(descriptor), mode)
            self.assertTrue(player._prepare_bot_vehicle_assignments(player_descriptor), mode)
            self.assertEqual(29, len(worker._bot_vehicle_assignments), mode)
            self.assertEqual(worker._bot_vehicle_assignments,
                             player._bot_vehicle_assignments, mode)
            self.assertEqual('germany:G16_PzVIB_Tiger_II',
                             worker._bot_vehicle_assignments[(1, 0)])

    def test_unavailable_pinned_vehicle_remains_rejected(self):
        battle, descriptor = make_battle(True, 'same')
        battle._start_message['bot_lineup'][0]['vehicle'] = 'germany:not_a_vehicle'
        self.assertFalse(battle._prepare_bot_vehicle_assignments(descriptor))
        self.assertEqual({}, battle._bot_vehicle_assignments)

    def test_excluded_vehicle_is_not_silently_restored(self):
        battle, descriptor = make_battle(True, 'same')
        battle._start_message['bot_lineup'] = []
        battle._start_message['bot_excluded_vehicles'] = ['germany:G16_PzVIB_Tiger_II']
        self.assertTrue(battle._prepare_bot_vehicle_assignments(descriptor))
        self.assertNotIn('germany:G16_PzVIB_Tiger_II',
                         list(battle._bot_vehicle_assignments.values()))


if __name__ == '__main__':
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(RosterContractTests))
        sys.exit(0 if result.wasSuccessful() else 1)
    finally:
        if TEMP:
            shutil.rmtree(TEMP)
