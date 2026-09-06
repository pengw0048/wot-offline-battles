import unittest

import bot_lineup_profiles


class BotLineupProfilesTests(unittest.TestCase):
    def test_profile_persists_one_fully_qualified_team_slot(self):
        store, name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "City duel")
        store = bot_lineup_profiles.set_assignment(
            store, name, 2, 3, "germany:G81_Pz_IV_AusfH")

        self.assertEqual(
            [{"team": 2, "slot": 3,
              "vehicle": "germany:G81_Pz_IV_AusfH"}],
            bot_lineup_profiles.assignments_for(store, name))

    def test_unqualified_saved_vehicle_is_rejected_instead_of_degrading(self):
        self.assertEqual(
            bot_lineup_profiles.empty_store(),
            bot_lineup_profiles.normalize_store({
                "schema": 1,
                "profiles": [{
                    "name": "Old profile",
                    "assignments": [{
                        "team": 1, "slot": 0,
                        "vehicle": "G81_Pz_IV_AusfH",
                    }],
                }],
            }))

    def test_eligible_choices_match_the_authority_exclusions(self):
        choices = [
            {"nation": "ussr", "vehicle": "R11_MS-1", "tags": ()},
            {"nation": "ussr", "vehicle": "R09_T-26_bot",
             "tags": ("lightTank", "secret", "unrecoverable")},
            {"nation": "japan", "vehicle": "J30_Edelweiss",
             "tags": ("mediumTank", "lockOutfit", "lockCrew",
                      "unrecoverable")},
            {"nation": "germany",
             "vehicle": "G138_VK168_02_Mauerbrecher", "tags": ()},
            {"nation": "ussr", "vehicle": "R98_T44_85",
             "tags": ("secret",)},
            {"nation": "ussr", "vehicle": "Observer",
             "tags": ("observer",)},
            {"nation": "germany", "vehicle": "Env_Artillery",
             "tags": ("SPG", "secret", "unrecoverable")},
            {"nation": "ussr", "vehicle": "EventTank",
             "tags": ("event_battles",)},
            {"nation": "germany", "vehicle": "IgrTank",
             "tags": ("premiumIGR",)},
            {"nation": "ussr", "vehicle": "R07_T-34-85_bootcamp",
             "tags": ("mediumTank", "secret")},
            {"nation": "ussr", "vehicle": "R45_IS-7_fallout",
             "tags": ("heavyTank", "fallout", "secret")},
        ]

        # A hidden entry with an honest level and name stays selectable; the
        # Bootcamp and Fallout copies of a real tank do not.
        self.assertEqual(
            ["ussr:R11_MS-1", "japan:J30_Edelweiss", "ussr:R98_T44_85"],
            [choice["type_name"] for choice in
             bot_lineup_profiles.eligible_vehicle_choices(choices)])

    def test_choice_name_preserves_nation_and_vehicle(self):
        self.assertEqual(
            "germany:G81_Pz_IV_AusfH",
            bot_lineup_profiles.vehicle_type_name({
                "nation": "germany", "vehicle": "G81_Pz_IV_AusfH",
            }))

    def test_automatic_profile_has_no_overrides(self):
        self.assertEqual([], bot_lineup_profiles.assignments_for(
            bot_lineup_profiles.empty_store(),
            bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL))
