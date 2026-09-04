from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(
    ROOT / "src" / "res" / "scripts" / "client"))

from gui.mods.offline_lan_0922.ai import planner  # noqa: E402


class BotTierModeTests(unittest.TestCase):
    def test_each_explicit_preset_has_the_requested_tier_band(self):
        available = range(1, 11)
        self.assertEqual((6,), planner.bot_match_tiers(
            6, "same", available_tiers=available))
        self.assertEqual((5, 6), planner.bot_match_tiers(
            6, "minus1_0", available_tiers=available))
        self.assertEqual((6, 7), planner.bot_match_tiers(
            6, "0_plus1", available_tiers=available))
        self.assertEqual((5, 6, 7, 8), planner.bot_match_tiers(
            6, "minus1_plus2", available_tiers=available))

    def test_explicit_preset_clamps_to_real_tiers(self):
        self.assertEqual((1, 2, 3), planner.bot_match_tiers(
            1, "minus1_plus2", available_tiers=range(1, 11)))
        self.assertEqual((9, 10), planner.bot_match_tiers(
            10, "minus1_0", available_tiers=range(1, 11)))

    def test_random_preserves_the_existing_three_tier_candidate_pool(self):
        self.assertTrue(planner.vehicle_in_bot_tier_mode(6, 5, "random"))
        self.assertTrue(planner.vehicle_in_bot_tier_mode(6, 7, "random"))
        self.assertFalse(planner.vehicle_in_bot_tier_mode(6, 8, "random"))
