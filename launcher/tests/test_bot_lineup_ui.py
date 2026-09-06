import unittest
from unittest import mock

import bot_lineup_profiles
import bot_lineup_ui


class _Variable(object):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo(object):
    def __init__(self, index=-1):
        self.index = index
        self.options = {}

    def config(self, **options):
        self.options.update(options)

    def current(self):
        return self.index


def _row():
    return {
        "nation": _Variable(), "vehicle": _Variable(), "skill": _Variable(),
        "nation_box": _Combo(), "vehicle_box": _Combo(),
        "skill_box": _Combo(),
    }


class BotLineupUITests(unittest.TestCase):
    def test_restore_resolves_the_complete_nation_vehicle_identity(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Saved")
        store = bot_lineup_profiles.set_assignment(
            store, profile_name, 2, 0, "germany:G12_Ltraktor")
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._game_root = "/game"
        editor._store = store
        editor._profile_name = profile_name
        editor.status = _Variable()
        editor._rows = {(2, 0): _row()}
        choices = [
            {"nation": "ussr", "vehicle": "G12_Ltraktor",
             "member": "ussr.xml", "label": "Other",
             "tags": ("lightTank",)},
            {"nation": "germany", "vehicle": "G12_Ltraktor",
             "member": "germany.xml", "label": "Leichttraktor",
             "tags": ("lightTank",)},
        ]

        with mock.patch.object(
                bot_lineup_ui.vehicle_overlays, "list_vehicle_choices",
                return_value=choices):
            editor._load_choices()

        row = editor._rows[(2, 0)]
        self.assertEqual("germany", row["nation"].get())
        self.assertEqual("Leichttraktor", row["vehicle"].get())
        self.assertEqual(
            ("Leichttraktor",), row["vehicle_box"].options["values"])
        self.assertEqual("Room preset", row["skill"].get())

    def test_a_saved_skill_tier_comes_back_into_the_editor(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Saved")
        store = bot_lineup_profiles.set_assignment(
            store, profile_name, 2, 0, None, "elite")
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._game_root = "/game"
        editor._store = store
        editor._profile_name = profile_name
        editor.status = _Variable()
        editor._rows = {(2, 0): _row()}

        with mock.patch.object(
                bot_lineup_ui.vehicle_overlays, "list_vehicle_choices",
                return_value=[]):
            editor._load_choices()

        self.assertEqual("Elite", editor._rows[(2, 0)]["skill"].get())

    def test_choosing_a_skill_alone_pins_the_slot_without_a_vehicle(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Saved")
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._store = store
        editor._profile_name = profile_name
        editor._choices_by_nation = {}
        editor.status = _Variable()
        saved = []
        editor._on_save = saved.append
        row = _row()
        row["skill"].set("Rookie")
        editor._rows = {(1, 3): row}

        editor._skill_changed((1, 3))

        self.assertEqual(
            [{"team": 1, "slot": 3, "skill": "rookie"}],
            bot_lineup_profiles.assignments_for(saved[-1], profile_name))

    def test_returning_a_slot_to_the_room_preset_clears_it(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Saved")
        store = bot_lineup_profiles.set_assignment(
            store, profile_name, 1, 3, None, "rookie")
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._store = store
        editor._profile_name = profile_name
        editor._choices_by_nation = {}
        editor.status = _Variable()
        saved = []
        editor._on_save = saved.append
        editor._rows = {(1, 3): _row()}

        editor._skill_changed((1, 3))

        self.assertEqual(
            [], bot_lineup_profiles.assignments_for(saved[-1], profile_name))


if __name__ == "__main__":
    unittest.main()
