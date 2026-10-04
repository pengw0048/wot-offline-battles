from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(
    ROOT / "src" / "res" / "scripts" / "client"))

from gui.mods.offline_lan_0922.ai import planner  # noqa: E402
from gui.mods.offline_lan_0922 import lan_client, waiting_room_ui  # noqa: E402


class BotTierModeTests(unittest.TestCase):
    def test_each_explicit_preset_has_the_requested_tier_band(self):
        available = range(1, 11)
        self.assertEqual((6,), planner.bot_match_tiers(
            6, "same", available_tiers=available))
        self.assertEqual((5, 6), planner.bot_match_tiers(
            6, "minus1_0", available_tiers=available))
        self.assertEqual((6, 7), planner.bot_match_tiers(
            6, "0_plus1", available_tiers=available))
        self.assertEqual((5, 6, 7), planner.bot_match_tiers(
            6, "minus1_plus1", available_tiers=available))
        self.assertEqual((6, 7, 8), planner.bot_match_tiers(
            6, "0_plus2", available_tiers=available))
        self.assertEqual((4, 5, 6), planner.bot_match_tiers(
            6, "minus2_0", available_tiers=available))

    def test_explicit_preset_clamps_to_real_tiers(self):
        self.assertEqual((1, 2), planner.bot_match_tiers(
            1, "minus1_plus1", available_tiers=range(1, 11)))
        self.assertEqual((9, 10), planner.bot_match_tiers(
            10, "minus1_0", available_tiers=range(1, 11)))
        self.assertEqual((1,), planner.bot_match_tiers(1, "minus2_0"))
        self.assertEqual((9, 10), planner.bot_match_tiers(9, "0_plus2"))
        self.assertEqual((10,), planner.bot_match_tiers(10, "0_plus2"))

    def test_random_admits_top_middle_and_bottom_tier_candidates(self):
        for level in range(4, 9):
            self.assertTrue(planner.vehicle_in_bot_tier_mode(6, level, "random"))
        for level in (3, 9):
            self.assertFalse(planner.vehicle_in_bot_tier_mode(6, level, "random"))
        self.assertEqual((4, 5, 6), planner.choose_match_tiers(6, 0.9, 0.0))
        self.assertEqual((5, 6, 7), planner.choose_match_tiers(6, 0.9, 0.5))
        self.assertEqual((6, 7, 8), planner.choose_match_tiers(6, 0.9, 1.0))

    def test_random_uses_only_one_contiguous_window_at_all_tiers(self):
        for player in range(1, 11):
            for mode_roll in (0.0, 0.28, 0.71, 0.72, 1.0):
                for side_roll in (0.0, 0.25, 0.5, 0.75, 1.0):
                    tiers = planner.choose_match_tiers(player, mode_roll, side_roll)
                    self.assertIn(player, tiers)
                    self.assertLessEqual(len(tiers), 3)
                    self.assertEqual(tuple(range(min(tiers), max(tiers) + 1)), tiers)
                    self.assertGreaterEqual(min(tiers), 1)
                    self.assertLessEqual(max(tiers), 10)

    def test_random_does_not_bridge_missing_catalog_tiers(self):
        self.assertEqual((6,), planner.choose_match_tiers(
            6, 0.9, 0.9, available_tiers=(4, 6, 8)))
        self.assertEqual((6, 7), planner.choose_match_tiers(
            6, 0.9, 0.9, available_tiers=(6, 7)))

    def test_random_contains_lan_humans_before_choosing_a_window(self):
        for mode_roll in (0.0, 0.5, 0.9):
            for side_roll in (0.0, 0.5, 1.0):
                self.assertEqual((6, 7, 8), planner.bot_match_tiers(
                    6, 'random', mode_roll, side_roll, required_tiers=(6, 8)))
                self.assertEqual((4, 5, 6), planner.bot_match_tiers(
                    6, 'random', mode_roll, side_roll, required_tiers=(4, 6)))

    def test_deliberately_wide_human_selection_does_not_add_further_tiers(self):
        self.assertEqual((4, 8), planner.bot_match_tiers(
            4, 'random', 0.9, 1.0, required_tiers=(4, 8)))
        self.assertEqual((6,), planner.bot_match_tiers(
            6, 'same', required_tiers=(8,)))

    def test_waiting_room_wire_and_planner_accept_the_same_presets(self):
        expected = set(planner.BOT_TIER_MODES)
        self.assertEqual(expected, set(lan_client.BOT_TIER_MODES))
        self.assertEqual(expected, set(mode for mode, label
                                       in waiting_room_ui.BOT_TIER_OPTIONS))
