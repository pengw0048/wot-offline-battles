"""Retired hidden vehicles stay player-accessible but never enter Bot lineups."""
import unittest
import sys
import types
from pathlib import Path

from launcher import bot_lineup_profiles, retired_vehicles


RETIRED = retired_vehicles.RETIRED_BOT_VEHICLES_0922
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src/res/scripts/client'), str(ROOT / 'server')]
from gui.mods.offline_lan_0922 import vehicle_configuration
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from lan_battle_server import _bot_lineup_allowed_names


class RetiredVehiclePolicyTests(unittest.TestCase):
    def test_retired_player_tank_is_not_an_unloadable_bot_substitute(self):
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._prepared_vehicle_names = ['ussr:R75_SU122_54', 'ussr:R11_MS-1']
        battle._config = {'vehicle': 'ussr:R75_SU122_54'}
        self.assertEqual(['usa:unloadable', 'ussr:R11_MS-1'],
                         list(battle._descriptor_candidates('usa:unloadable')))
        self.assertEqual('ussr:R75_SU122_54', next(
            battle._descriptor_candidates('ussr:R75_SU122_54')))

    def test_launcher_worker_and_server_agree_without_hiding_player_vehicles(self):
        self.assertEqual(RETIRED, vehicle_configuration.RETIRED_BOT_VEHICLES)
        catalog = [dict(name=name, tags=['AT-SPG']) for name in RETIRED]
        catalog.append(dict(name='ussr:R11_MS-1', tags=['lightTank']))
        self.assertEqual({'ussr:R11_MS-1'}, _bot_lineup_allowed_names(catalog))
        for name in RETIRED:
            entry = types.SimpleNamespace(name=name, tags=('secret', 'AT-SPG'))
            self.assertTrue(BattleRuntime._vehicle_excluded(entry))
            self.assertTrue(vehicle_configuration.is_standard_battle_vehicle(entry))
    def test_exact_bot_lineup_drops_retired_vehicle_but_keeps_skill(self):
        store = {
            "schema": bot_lineup_profiles.SCHEMA,
            "profiles": [{
                "name": "legacy",
                "assignments": [{
                    "team": 1,
                    "slot": 3,
                    "vehicle": "germany:G98_Waffentrager_E100",
                    "skill": "veteran",
                }],
            }],
        }
        normalized = bot_lineup_profiles.normalize_store(store)
        self.assertEqual([{
            "team": 1, "slot": 3, "skill": "veteran",
        }], normalized["profiles"][0]["assignments"])

    def test_retired_vehicles_are_not_bot_candidates(self):
        for type_name in RETIRED:
            nation, vehicle = type_name.split(":", 1)
            with self.subTest(type_name=type_name):
                choice = {
                    "nation": nation,
                    "vehicle": vehicle,
                    "tags": ["mediumTank"],
                }
                self.assertFalse(bot_lineup_profiles.vehicle_choice_is_eligible(choice))
                self.assertTrue(bot_lineup_profiles.vehicle_choice_is_standard(choice))

    def test_non_retired_hidden_vehicle_is_not_excluded_by_name_policy(self):
        self.assertTrue(bot_lineup_profiles.vehicle_choice_is_eligible({
            "nation": "germany",
            "vehicle": "G98_Waffentrager_E100_P",
            "tags": ["AT-SPG", "secret"],
        }))


if __name__ == "__main__":
    unittest.main()
