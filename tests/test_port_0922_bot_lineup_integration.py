from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_ROOT = ROOT / "launcher"
PORT_ROOT = ROOT
CLIENT_ROOT = PORT_ROOT / "src" / "res" / "scripts" / "client"
SERVER_ROOT = PORT_ROOT / "server"
for path in (LAUNCHER_ROOT, SERVER_ROOT, CLIENT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import bot_lineup_profiles  # noqa: E402
import bot_lineup_ui  # noqa: E402
import vehicle_overlays  # noqa: E402
import core as launcher_core  # noqa: E402
import lan_battle_server as server_runtime  # noqa: E402
import windows_server  # noqa: E402
from gui.mods.offline_lan_0922 import vehicle_blacklist  # noqa: E402
from gui.mods.offline_lan_0922 import descriptor_donation  # noqa: E402
from gui.mods.offline_lan_0922 import vehicle_configuration  # noqa: E402
from gui.mods.offline_lan_0922.ai import planner as bot_planner  # noqa: E402
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
    def test_default_bot_lineup_has_no_artillery_and_keeps_tank_destroyers(self):
        artillery = {'name': 'ussr:artillery', 'tags': ('SPG',)}
        destroyer = {'name': 'ussr:destroyer', 'tags': ('AT-SPG',)}

        selected = bot_planner.select_bot_lineup(
            [artillery, destroyer], 15)

        self.assertEqual(15, len(selected))
        self.assertEqual({'ussr:destroyer'},
                         {row['name'] for row in selected})

    def test_zero_artillery_cap_replaces_a_mirrored_human_artillery_slot(self):
        artillery = {'name': 'ussr:artillery', 'tags': ('SPG',)}
        destroyer = {'name': 'ussr:destroyer', 'tags': ('AT-SPG',)}

        selected = bot_planner.select_bot_lineup(
            [artillery], 15, spg_limit=0,
            fallback_candidates=[artillery, destroyer])

        self.assertEqual([destroyer] * 15, selected)

    def test_automatic_rosters_have_zero_artillery_without_removing_humans(self):
        entries = {}
        for level in range(1, 11):
            for class_tag in bot_planner.MATCH_CLASSES:
                entries[len(entries)] = types.SimpleNamespace(
                    name='ussr:%s_%d' % (class_tag, level), level=level,
                    tags=(class_tag,))
        by_name = {entry.name: entry for entry in entries.values()}
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('all',), INDICES={'all': 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)))
        bots = [
            {'id': team * 100 + slot, 'team': team, 'slot': slot}
            for team in (1, 2) for slot in range(1 if team == 1 else 0, 15)
        ]
        for level in range(1, 11):
            for mode in bot_planner.BOT_TIER_MODES:
                for human_class in ('SPG', 'heavyTank'):
                    player_name = 'ussr:%s_%d' % (human_class, level)
                    descriptor = types.SimpleNamespace(type=by_name[player_name])
                    assignments = []
                    for worker_mode in (False, True):
                        battle = BattleRuntime.__new__(BattleRuntime)
                        battle._runtime = runtime
                        battle._worker_mode = worker_mode
                        battle._config = {'vehicle': player_name}
                        battle._start_message = {
                            'round_id': 17, 'map': '01_karelia',
                            'players': [{'id': 1, 'team': 1, 'slot': 0,
                                         'vehicle': player_name}],
                            'bots': bots, 'bot_tier_mode': mode,
                        }
                        battle.client = types.SimpleNamespace(team=1, player_id=1)
                        battle._resolve_descriptor = lambda unused: descriptor

                        self.assertTrue(battle._prepare_bot_vehicle_assignments(
                            descriptor), (level, mode, human_class, worker_mode))

                        selected = battle._bot_vehicle_assignments
                        self.assertEqual(29, len(selected))
                        self.assertFalse(any('SPG' in by_name[name].tags
                                             for name in selected.values()),
                                         (level, mode, human_class, worker_mode))
                        self.assertEqual((human_class,), descriptor.type.tags)
                        assignments.append(selected)
                    self.assertEqual(assignments[0], assignments[1])

    def test_explicit_artillery_override_is_preserved(self):
        entries = {
            1: types.SimpleNamespace(name='ussr:regular', level=8,
                                     tags=('mediumTank',)),
            2: types.SimpleNamespace(name='ussr:artillery', level=8,
                                     tags=('SPG',)),
        }
        descriptor = types.SimpleNamespace(type=entries[1])
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('all',), INDICES={'all': 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)))
        battle._worker_mode = True
        battle._config = {'vehicle': 'ussr:regular'}
        battle._start_message = {
            'round_id': 17, 'map': '01_karelia', 'bot_tier_mode': 'same',
            'players': [{'id': 1, 'team': 1, 'slot': 0,
                         'vehicle': 'ussr:regular'}],
            'bots': [{'id': 11, 'team': 1, 'slot': 1},
                     {'id': 21, 'team': 2, 'slot': 0}],
            'bot_lineup': [{'team': 2, 'slot': 0,
                           'vehicle': 'ussr:artillery'}],
        }
        battle.client = types.SimpleNamespace(team=1, player_id=1)
        battle._resolve_descriptor = lambda unused: descriptor

        self.assertTrue(battle._prepare_bot_vehicle_assignments(descriptor))

        self.assertEqual({(1, 1): 'ussr:regular', (2, 0): 'ussr:artillery'},
                         battle._bot_vehicle_assignments)

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
        server_lineup = windows_server._bot_lineup_from_environment(
            environment)
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
            "bot_tier_mode": "same",
            "bot_lineup": server_lineup,
        }
        battle.client = types.SimpleNamespace(team=1, player_id=1)
        battle._resolve_descriptor = descriptors.__getitem__

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
            7: types.SimpleNamespace(
                name='ussr:R07_T-34-85_bootcamp', level=2,
                tags=('mediumTank', 'secret')),
            8: types.SimpleNamespace(
                name='ussr:R45_IS-7_fallout', level=10,
                tags=('heavyTank', 'fallout', 'secret')),
        }
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('all',), INDICES={'all': 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(
                getList=lambda unused_nation_id: entries)))

        # A hidden entry keeps its own honest level and name, so it stays in
        # every catalogue.  The Bootcamp copy publishes the tier 6 T-34-85 at
        # level 2 and the Fallout copy is event content; neither may reach the
        # garage, the waiting-room roster, or a tier-matched Bot lineup.
        self.assertEqual(
            ['ussr:R11_MS-1', 'ussr:R99_SecretTank'],
            [row['name'] for row in
             descriptor_donation.vehicle_catalog(runtime)])
        for entry in list(entries.values())[1:5]:
            self.assertFalse(
                vehicle_configuration.is_standard_battle_vehicle(entry))
        self.assertTrue(vehicle_configuration.is_standard_battle_vehicle(
            entries[6]))
        for key in (7, 8):
            self.assertFalse(
                vehicle_configuration.is_standard_battle_vehicle(
                    entries[key]))
            self.assertFalse(bot_lineup_profiles.vehicle_choice_is_eligible({
                'nation': entries[key].name.split(':')[0],
                'vehicle': entries[key].name.split(':')[1],
                'tags': entries[key].tags,
            }))

    # The launcher reaches its Bot roster through two filters: the editor's
    # own ``selectable`` pass in vehicle_overlays.list_vehicle_choices, then
    # bot_lineup_profiles.eligible_vehicle_choices.  The mod applies one and
    # the server validates explicit choices against the donated catalogue.
    # Comparing constants alone would not prove the composition matches, so
    # this reproduces all three effective predicates over the exact families.
    _EXCLUSION_CASES = (
        ('ussr:R11_MS-1', ('lightTank',), True),
        ('ussr:R99_SecretTank', ('mediumTank', 'secret'), True),
        ('ussr:R07_T-34-85_bootcamp', ('mediumTank', 'secret'), False),
        ('ussr:R45_IS-7_fallout', ('heavyTank', 'fallout', 'secret'), False),
        ('ussr:R07_T-34-85_training',
         ('mediumTank', 'secret', 'unrecoverable'), False),
        ('germany:Env_Artillery', ('SPG', 'secret', 'unrecoverable'), False),
        ('germany:G01_Maus_IGR', ('heavyTank', 'premiumIGR', 'secret'), False),
        ('ussr:EventTank', ('mediumTank', 'event_battles'), False),
        ('ussr:Observer', ('lightTank', 'observer'), False),
        ('usa:T23', ('mediumTank',), False),
        ('germany:G138_VK168_02_Mauerbrecher', ('heavyTank',), False),
    )

    @staticmethod
    def _launcher_admits(name, tags):
        """Reproduce the composed launcher decision for one vehicle."""
        if (vehicle_overlays._NON_EDITABLE_VEHICLE_TAGS.intersection(tags) or
                name in vehicle_overlays._NON_EDITABLE_VEHICLES):
            return False
        nation, vehicle = name.split(':', 1)
        return bool(bot_lineup_profiles.eligible_vehicle_choices([{
            'nation': nation, 'vehicle': vehicle, 'tags': list(tags),
        }]))

    def test_launcher_and_server_share_the_mod_exclusion_rule(self):
        self.assertEqual(
            frozenset(vehicle_configuration.NON_STANDARD_BATTLE_TAGS),
            frozenset(bot_lineup_profiles.NON_STANDARD_BOT_TAGS_0922))
        self.assertEqual(
            frozenset(vehicle_configuration.NON_STANDARD_BATTLE_NAMES),
            frozenset(bot_lineup_profiles.NON_STANDARD_BOT_VEHICLES_0922))
        self.assertEqual(
            tuple(vehicle_configuration.CLONE_NAME_SUFFIXES),
            tuple(bot_lineup_profiles.CLONE_BOT_VEHICLE_SUFFIXES_0922))
        self.assertEqual(
            vehicle_configuration.CATALOGUE_VISIBILITY_TAG,
            bot_lineup_profiles.CATALOGUE_VISIBILITY_TAG_0922)
        # The editor pass may never be the stricter of the two, or the
        # launcher would silently withhold a Bot the worker accepts.
        self.assertLessEqual(
            frozenset(vehicle_overlays._NON_EDITABLE_VEHICLE_TAGS),
            frozenset(bot_lineup_profiles.NON_STANDARD_BOT_TAGS_0922))
        self.assertLessEqual(
            frozenset(vehicle_overlays._NON_EDITABLE_VEHICLES),
            frozenset(bot_lineup_profiles.NON_STANDARD_BOT_VEHICLES_0922))

        for name, tags, expected in self._EXCLUSION_CASES:
            entry = types.SimpleNamespace(name=name, level=5, tags=tags)
            admitted = bool(
                vehicle_configuration.is_standard_battle_vehicle(entry) and
                not vehicle_blacklist.is_unusable(name))
            self.assertEqual(expected, admitted, name)
            self.assertEqual(
                admitted, self._launcher_admits(name, tags), name)
            server_names = server_runtime._bot_lineup_allowed_names([{
                'name': name, 'level': 5, 'tags': list(tags),
            }])
            self.assertEqual(admitted, name in server_names, name)

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
            "bot_tier_mode": "same", "bot_lineup": lineup,
        }
        battle.client = types.SimpleNamespace(team=1, player_id=1)

        self.assertFalse(battle._prepare_bot_vehicle_assignments(
            _descriptor("ussr:R11_MS-1")))
        self.assertEqual({}, battle._bot_vehicle_assignments)


if __name__ == "__main__":
    unittest.main()
