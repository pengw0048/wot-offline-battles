import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_ROOT = ROOT / "launcher"
PORT_ROOT = ROOT / "0.9.22"
CLIENT_ROOT = PORT_ROOT / "src" / "res" / "scripts" / "client"
for path in (LAUNCHER_ROOT, CLIENT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import bot_lineup_profiles  # noqa: E402
import bot_lineup_ui  # noqa: E402
import core as launcher_core  # noqa: E402
from gui.mods.offline_lan_0922 import vehicle_blacklist  # noqa: E402
from gui.mods.offline_lan_0922 import descriptor_donation  # noqa: E402
from gui.mods.offline_lan_0922 import vehicle_configuration  # noqa: E402
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime  # noqa: E402


class _Variable(object):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo(object):
    def __init__(self, index=0):
        self.index = index

    def current(self):
        return self.index


def _descriptor(name):
    return types.SimpleNamespace(type=types.SimpleNamespace(
        name=name, level=1, tags=("lightTank",)))


class BotLineupIntegrationTests(unittest.TestCase):
    def _ui_saved_assignment(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Exact duel")
        choice = bot_lineup_profiles.eligible_vehicle_choices([{
            "nation": "germany",
            "vehicle": "G12_Ltraktor",
            "member": "vehicles/germany/G12_Ltraktor.xml",
            "label": "Leichttraktor",
            "tags": ("lightTank",),
        }])[0]
        saved = []
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._profile_name = profile_name
        editor._store = store
        editor._choices_by_nation = {"germany": [choice]}
        editor._rows = {(2, 0): {
            "nation": _Variable("germany"),
            "vehicle": _Variable("Leichttraktor"),
            "vehicle_box": _Combo(0),
        }}
        editor._on_save = saved.append
        editor.status = _Variable()

        editor._vehicle_changed((2, 0))

        self.assertEqual(1, len(saved))
        return bot_lineup_profiles.assignments_for(saved[0], profile_name)

    def test_ui_profile_env_and_hidden_worker_keep_the_exact_type_name(self):
        assignments = self._ui_saved_assignment()
        expected_name = "germany:G12_Ltraktor"
        self.assertEqual(expected_name, assignments[0]["vehicle"])

        environment = launcher_core.server_environment(
            launcher_core.PORT_0_9_22, "/game", {},
            bot_lineup=assignments)
        server_lineup = json.loads(environment["WOT_0922_BOT_LINEUP"])
        self.assertEqual(assignments, server_lineup)

        roster = ({"id": 21, "team": 2, "slot": 0},)
        entries = {
            1: types.SimpleNamespace(
                name="ussr:R11_MS-1", level=1,
                tags=("lightTank",)),
            2: types.SimpleNamespace(
                name=expected_name, level=1, tags=("lightTank",)),
        }
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=("all",), INDICES={"all": 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)),
        )
        descriptors = dict(
            (name, _descriptor(name)) for name in
            ("ussr:R11_MS-1", expected_name))
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._runtime = runtime
        battle._worker_mode = True
        battle._config = {"vehicle": "ussr:R11_MS-1"}
        battle._start_message = {
            "round_id": 1,
            "map": "01_karelia",
            "players": [{
                "id": 1, "team": 1, "slot": 0,
                "vehicle": "ussr:R11_MS-1",
            }],
            "bots": list(roster),
            "bot_manifest": [{
                "id": 21, "team": 2, "slot": 0,
                "vehicle": expected_name,
            }],
            "bot_tier_mode": "same",
            "bot_lineup": server_lineup,
        }
        battle.client = types.SimpleNamespace(team=1, player_id=1)
        battle._resolve_descriptor = descriptors.__getitem__
        battle._resolve_canonical_bot_descriptor = descriptors.__getitem__

        self.assertTrue(battle._prepare_bot_vehicle_assignments(
            descriptors["ussr:R11_MS-1"]))
        self.assertEqual(
            expected_name, battle._bot_vehicle_assignments[(2, 0)])

    def test_launcher_blacklist_is_bound_to_the_hidden_worker_catalog(self):
        self.assertEqual(
            frozenset(vehicle_blacklist.UNUSABLE_VEHICLES),
            bot_lineup_profiles.UNUSABLE_BOT_VEHICLES_0922)

    def test_all_catalogues_share_the_standard_battle_exclusions(self):
        entries = {
            1: types.SimpleNamespace(
                name='ussr:R11_MS-1', level=1, tags=('lightTank',)),
            2: types.SimpleNamespace(
                name='germany:Env_Artillery', level=5,
                tags=('SPG', 'secret', 'unrecoverable')),
            3: types.SimpleNamespace(
                name='ussr:EventTank', level=5,
                tags=('mediumTank', 'event_battles')),
            4: types.SimpleNamespace(
                name='germany:IgrTank', level=5,
                tags=('heavyTank', 'premiumIGR')),
            5: types.SimpleNamespace(
                name='ussr:Observer', level=1,
                tags=('lightTank', 'observer')),
            6: types.SimpleNamespace(
                name='ussr:R99_SecretTank', level=8,
                tags=('mediumTank', 'secret')),
        }
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('all',), INDICES={'all': 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)))

        self.assertEqual(
            ['ussr:R11_MS-1', 'ussr:R99_SecretTank'],
            [row['name'] for row in
             descriptor_donation.vehicle_catalog(runtime)])
        for entry in list(entries.values())[1:5]:
            self.assertFalse(
                vehicle_configuration.is_standard_battle_vehicle(entry))
        self.assertTrue(vehicle_configuration.is_standard_battle_vehicle(
            entries[6]))

    def test_missing_exact_vehicle_is_rejected_by_hidden_worker(self):
        lineup = [{
            "team": 2, "slot": 0, "vehicle": "germany:Missing",
        }]
        roster = ({"id": 21, "team": 2, "slot": 0},)
        entries = {1: types.SimpleNamespace(
            name="ussr:R11_MS-1", level=1, tags=("lightTank",))}
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=("all",), INDICES={"all": 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)))
        battle._worker_mode = True
        battle._config = {"vehicle": "ussr:R11_MS-1"}
        battle._start_message = {
            "round_id": 1, "map": "01_karelia",
            "players": [], "bots": list(roster),
            "bot_manifest": [{
                "id": 21, "team": 2, "slot": 0,
                "vehicle": "germany:Missing",
            }],
            "bot_tier_mode": "same", "bot_lineup": lineup,
        }
        battle.client = types.SimpleNamespace(team=1, player_id=1)
        battle._resolve_descriptor = mock.Mock(
            side_effect=KeyError("germany:Missing"))
        battle._resolve_canonical_bot_descriptor = battle._resolve_descriptor

        self.assertFalse(battle._prepare_bot_vehicle_assignments(
            _descriptor("ussr:R11_MS-1")))
        self.assertEqual({}, battle._bot_vehicle_assignments)


if __name__ == "__main__":
    unittest.main()
