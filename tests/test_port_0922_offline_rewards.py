"""Offline economy tests: the published reward structure and its one anchor.

Wargaming never published ``X``, ``Y``, ``Z`` or a vehicle's profitability
coefficient, so the magnitudes here stay this product's policy.  What these
tests pin is the *structure* the published material does describe, plus the one
relationship recoverable from the captured retail tables: how XP per point of
damage falls with vehicle tier.
"""

import statistics as statistics_module
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))

import offline_rewards
from offline_rewards import compute_offline_rewards
from gui.mods.offline_lan_0922 import mastery_catalog


def _credits(**kwargs):
    statistics = {}
    for name in ('damage_dealt', 'damage_assisted_track',
                 'damage_assisted_radio', 'damage_assisted_stun', 'kills',
                 'spotted', 'capture_points', 'dropped_capture_points'):
        if name in kwargs:
            statistics[name] = kwargs.pop(name)
    return compute_offline_rewards(statistics, kwargs.pop('won', False),
                                   **kwargs)


class TierAnchorTests(unittest.TestCase):
    def test_tier_factors_are_the_baked_retail_tables_measured(self):
        """The constants must stay what the captured retail tables say."""
        index = mastery_catalog.MARKS_PERCENTILES.index(95)
        samples = {}
        for intcd, profile in mastery_catalog.VEHICLE_PROFILE.items():
            tier = profile[0]
            thresholds = mastery_catalog.MASTERY_XP.get(intcd)
            curve = mastery_catalog.MARKS_DAMAGE.get(intcd)
            if not thresholds or not curve or tier < 5:
                continue
            samples.setdefault(tier, []).append(
                thresholds[3] / float(curve[index]))
        self.assertEqual(set(range(5, 11)), set(samples))
        pivot = statistics_module.median(
            samples[offline_rewards.XP_TIER_PIVOT])
        for tier, values in samples.items():
            expected = int(round(
                statistics_module.median(values) / pivot * 1000))
            self.assertEqual(
                expected, offline_rewards.XP_TIER_PERMILLE[tier],
                'tier %d factor drifted from the baked retail tables' % tier)
        # Retail publishes no gun-mark data below Tier V, so the lowest tiers
        # hold the lowest measured value rather than extrapolating past it.
        for tier in (1, 2, 3, 4):
            self.assertEqual(offline_rewards.XP_TIER_PERMILLE[5],
                             offline_rewards.XP_TIER_PERMILLE[tier])
        self.assertEqual(1000, offline_rewards.XP_TIER_PERMILLE[
            offline_rewards.XP_TIER_PIVOT])

    def test_the_same_battle_pays_less_xp_as_tier_rises(self):
        battle = {'damage_dealt': 2000, 'kills': 3, 'spotted': 2}
        rewards = [compute_offline_rewards(
            battle, False, True, tier, killed_durability=4200)['xp']
            for tier in range(5, 11)]
        self.assertEqual(sorted(rewards, reverse=True), rewards)
        self.assertGreater(rewards[0], rewards[-1])
        # Spotting XP carries no tier relationship, so the ratio of the
        # damage-driven part is what moves.
        combat = (2000 // 5 +
                  4200 // offline_rewards.KILL_XP_DURABILITY_DIVISOR)
        for offset, tier in enumerate(range(5, 11)):
            expected = (offline_rewards.OFFLINE_PARTICIPATION_XP +
                        combat * offline_rewards.XP_TIER_PERMILLE[tier] //
                        1000 + 2 * 20)
            self.assertEqual(expected, rewards[offset])

    def test_a_kill_is_paid_by_what_was_killed(self):
        """Wargaming: a kill counts with the tier difference taken in."""
        base = compute_offline_rewards({}, False, True, 8)
        # The plain frag count carries no XP of its own any more.
        self.assertEqual(base['xp'], compute_offline_rewards(
            {'kills': 3}, False, True, 8)['xp'])
        small = compute_offline_rewards(
            {'kills': 1}, False, True, 8, killed_durability=420)['xp']
        large = compute_offline_rewards(
            {'kills': 1}, False, True, 8, killed_durability=2000)['xp']
        self.assertGreater(large, small)
        self.assertEqual(
            base['xp'] + 2000 // offline_rewards.KILL_XP_DURABILITY_DIVISOR,
            large)
        # A Tier VIII kill of a median Tier VIII vehicle keeps the 100 XP the
        # previous flat rule paid, which is how the divisor is pinned.
        self.assertEqual(base['xp'] + 100, compute_offline_rewards(
            {'kills': 1}, False, True, offline_rewards.XP_TIER_PIVOT,
            killed_durability=1400)['xp'])


class CreditStructureTests(unittest.TestCase):
    def test_only_the_participation_payment_carries_the_victory_factor(self):
        """Lesta support: only ``X * tier`` is multiplied by 1.85 on a win."""
        loss = _credits(damage_dealt=1000, spotted=1, vehicle_tier=6)
        win = _credits(damage_dealt=1000, spotted=1, vehicle_tier=6, won=True)
        participation = (
            offline_rewards.OFFLINE_PARTICIPATION_CREDITS_PER_TIER * 6)
        actions = (1000 * offline_rewards.OFFLINE_DAMAGE_CREDITS_PER_POINT +
                   offline_rewards.OFFLINE_SPOTTING_CREDITS)
        self.assertEqual(participation + actions, loss['credits'])
        self.assertEqual(
            participation * offline_rewards.OFFLINE_WIN_CREDITS_FACTOR_100
            // 100 + actions, win['credits'])

    def test_damage_credits_do_not_scale_with_tier(self):
        """Lesta support: ``Y`` per durability point is tier independent."""
        low = _credits(damage_dealt=500, vehicle_tier=2)
        high = _credits(damage_dealt=500, vehicle_tier=9)
        self.assertEqual(
            low['credits'] -
            offline_rewards.OFFLINE_PARTICIPATION_CREDITS_PER_TIER * 2,
            high['credits'] -
            offline_rewards.OFFLINE_PARTICIPATION_CREDITS_PER_TIER * 9)

    def test_detecting_an_spg_pays_double(self):
        """Lesta support: ``Z`` per detected tank, ``2 * Z`` for an SPG."""
        tanks = _credits(spotted=2, vehicle_tier=5)
        one_spg = _credits(spotted=2, spotted_spgs=1, vehicle_tier=5)
        both_spg = _credits(spotted=2, spotted_spgs=2, vehicle_tier=5)
        step = offline_rewards.OFFLINE_SPOTTING_CREDITS
        self.assertEqual(tanks['credits'] + step, one_spg['credits'])
        self.assertEqual(tanks['credits'] + 2 * step, both_spg['credits'])
        # An SPG count above the detection count cannot inflate the payment.
        self.assertEqual(both_spg['credits'],
                         _credits(spotted=2, spotted_spgs=9,
                                  vehicle_tier=5)['credits'])

    def test_capture_credits_need_a_completed_capture_and_split_equally(self):
        """Lesta support: paid only for a capture that completed, split."""
        points_only = _credits(capture_points=60, vehicle_tier=4)
        self.assertEqual(
            offline_rewards.OFFLINE_PARTICIPATION_CREDITS_PER_TIER * 4,
            points_only['credits'])
        alone = _credits(capture_points=100, vehicle_tier=4,
                         capture_participants=1)
        shared = _credits(capture_points=50, vehicle_tier=4,
                          capture_participants=4)
        self.assertEqual(points_only['credits'] +
                         offline_rewards.OFFLINE_CAPTURE_CREDITS,
                         alone['credits'])
        self.assertEqual(points_only['credits'] +
                         offline_rewards.OFFLINE_CAPTURE_CREDITS // 4,
                         shared['credits'])

    def test_assisted_damage_pays_xp_but_no_credits(self):
        """The published credits list has no assisted-damage payment."""
        plain = _credits(damage_dealt=800, vehicle_tier=7)
        assisted = _credits(damage_dealt=800, damage_assisted_radio=1500,
                            vehicle_tier=7)
        self.assertEqual(plain['credits'], assisted['credits'])
        self.assertGreater(assisted['xp'], plain['xp'])

    def test_free_xp_is_five_percent_and_costs_stay_zero(self):
        rewards = _credits(damage_dealt=1200, kills=2, won=True,
                           vehicle_tier=8)
        self.assertEqual(rewards['xp'] * 5 // 100, rewards['free_xp'])
        self.assertEqual(0, rewards['repair_cost'])
        self.assertEqual(0, rewards['ammo_cost'])

    def test_bad_inputs_cannot_produce_a_negative_or_unbounded_payment(self):
        rewards = compute_offline_rewards(
            {'damage_dealt': -50, 'spotted': None, 'kills': 'three'},
            False, True, vehicle_tier=None, spotted_spgs=-3,
            capture_participants=-1)
        self.assertEqual(
            offline_rewards.OFFLINE_PARTICIPATION_CREDITS_PER_TIER,
            rewards['credits'])
        # Nothing countable survived, so only the participation XP remains.
        self.assertEqual(offline_rewards.OFFLINE_PARTICIPATION_XP,
                         rewards['xp'])
        self.assertEqual(0, compute_offline_rewards(
            {}, False, participated=False)['credits'])


class ServerRewardInputTests(unittest.TestCase):
    """The two reward inputs the server derives from its own ledgers."""

    def _state(self):
        from lan_battle_server import BattleState, CLIENT_BUILD_0922
        state = BattleState(map_name='13_erlenberg')
        state.client_build = CLIENT_BUILD_0922
        state.vehicle_catalogs = {1: (
            {'name': 'ussr:R04_T-34', 'level': 5, 'tags': ('mediumTank',)},
            {'name': 'ussr:R28_SU_26', 'level': 3, 'tags': ('SPG',)},
        )}
        state.bot_manifest = [
            {'id': 11, 'name': 'Bot 11', 'vehicle': 'ussr:R04_T-34',
             'team': 2},
            {'id': 12, 'name': 'Bot 12', 'vehicle': 'ussr:R28_SU_26',
             'team': 2},
            {'id': 13, 'name': 'Bot 13', 'vehicle': 'ussr:R04_T-34',
             'team': 1},
        ]
        state.bot_states = dict(
            (int(row['id']), dict(row, alive=True, health=100))
            for row in state.bot_manifest)
        return state

    def test_only_artillery_detections_count_as_spg_spots(self):
        state = self._state()
        for target in (('bot', 11), ('bot', 12)):
            state._statistics_interaction(('bot', 13), target)['spotted'] = 1
        self.assertEqual(1, state._spotted_spg_count('bot', 13))
        # A recorded target that was never revealed does not count.
        state._statistics_interaction(('bot', 13), ('bot', 12))['spotted'] = 0
        self.assertEqual(0, state._spotted_spg_count('bot', 13))
        self.assertEqual(0, state._spotted_spg_count('bot', 11))

    def test_killed_durability_sums_the_victims_own_health(self):
        state = self._state()
        # Bot 13 destroys one medium and one SPG on the other team.
        state.bot_states[11]['max_health'] = 900
        state.bot_states[12]['max_health'] = 240
        for target in (('bot', 11), ('bot', 12)):
            state._statistics_interaction(('bot', 13), target)[
                'target_kills'] = 1
        self.assertEqual(1140, state._killed_durability('bot', 13))
        # A target that was damaged but never destroyed adds nothing.
        state._statistics_interaction(('bot', 13), ('bot', 11))[
            'target_kills'] = 0
        self.assertEqual(240, state._killed_durability('bot', 13))
        self.assertEqual(0, state._killed_durability('bot', 11))

    def test_capture_split_needs_a_completed_capture_and_participation(self):
        state = self._state()
        state._statistics_row('bot', 11)['capture_points'] = 40
        state._statistics_row('bot', 12)['capture_points'] = 60
        state._statistics_row('bot', 13)['capture_points'] = 25
        # No capture completed yet.
        self.assertEqual(0, state._capture_participants('bot', 11, 2))
        state.base_captured_team = 2
        self.assertEqual(2, state._capture_participants('bot', 11, 2))
        self.assertEqual(2, state._capture_participants('bot', 12, 2))
        # The losing team's own capture progress earns nothing.
        self.assertEqual(0, state._capture_participants('bot', 13, 1))
        # A team mate that never touched the base earns nothing either.
        state._statistics_row('bot', 11)['capture_points'] = 0
        self.assertEqual(0, state._capture_participants('bot', 11, 2))
        self.assertEqual(1, state._capture_participants('bot', 12, 2))


if __name__ == '__main__':
    unittest.main()
