"""Contract tests for the Bot gunnery skill tiers and their presets."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
    'offline_lan_0922')
sys.path.insert(0, str(ROOT / 'launcher'))


def _load(module_name):
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    full_name = 'gui.mods.offline_lan_0922.%s' % module_name
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(
        full_name, PACKAGE_ROOT / ('%s.py' % module_name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


gunnery = _load('bot_gunnery')

ROOKIE = gunnery.rating_for_skill(gunnery.SKILL_ROOKIE)
REGULAR = gunnery.rating_for_skill(gunnery.SKILL_REGULAR)
VETERAN = gunnery.rating_for_skill(gunnery.SKILL_VETERAN)
ELITE = gunnery.rating_for_skill(gunnery.SKILL_ELITE)


class SkillTierTableTests(unittest.TestCase):
    def test_the_tiers_are_ordered_from_the_weakest_to_the_best_gunner(self):
        """Every knob has to move the same way, or a preset means nothing."""
        rows = [gunnery.skill_parameters(skill)
                for skill in gunnery.SKILL_TIERS]
        for name in ('reaction_seconds', 'converged_factor',
                     'aim_bias_factor', 'lead_error'):
            values = [row[name] for row in rows]
            self.assertEqual(sorted(values, reverse=True), values, name)
            self.assertEqual(len(set(values)), len(values), name)
        for name in ('patience_seconds',):
            values = [row[name] for row in rows]
            self.assertEqual(sorted(values), values, name)
            self.assertEqual(len(set(values)), len(values), name)
        # The crew level only has to never fall, because it is the one knob
        # that also moves view range and reload; tiers are allowed to share
        # a full crew and separate on gunnery alone.
        levels = [row['crew_level'] for row in rows]
        self.assertEqual(sorted(levels), levels)

    def test_every_crew_level_is_one_1513_can_train(self):
        for skill in gunnery.SKILL_TIERS:
            level = gunnery.crew_level(skill)
            self.assertGreaterEqual(level, 50)
            self.assertLessEqual(level, 100)
        self.assertEqual(100, gunnery.crew_level(gunnery.SKILL_ELITE))

    def test_only_the_two_weaker_tiers_train_below_a_full_crew(self):
        """The crew level is deliberately not the knob that ranks tiers.

        It also shortens view range and slows reload, so spending it on every
        tier would turn a gunnery preset into a spotting preset.  Veteran and
        elite share #1513's full crew and separate on the gunner model alone.
        """
        self.assertEqual(
            [75, 90, 100, 100],
            [gunnery.crew_level(skill) for skill in gunnery.SKILL_TIERS])

    def test_unknown_names_fall_back_instead_of_raising(self):
        for value in (None, '', 'perfect', 7, object()):
            self.assertEqual(gunnery.DEFAULT_SKILL,
                             gunnery.normalize_skill(value))
            self.assertEqual(gunnery.DEFAULT_SKILL_MODE,
                             gunnery.normalize_skill_mode(value))

    def test_every_preset_is_a_usable_rating_distribution(self):
        for mode in gunnery.SKILL_MODES:
            points = gunnery._MODE_RATING_POINTS[mode]
            self.assertEqual(5, len(points), mode)
            self.assertTrue(
                all(0.0 <= point <= 1.0 for point in points), mode)
            # An inverse CDF may not run backwards.
            self.assertEqual(sorted(points), list(points), mode)

    def test_each_preset_hits_the_mean_rating_it_specifies(self):
        """The presets are specified by their mean, not by their shape."""
        expected = {'easy': 0.10, 'relaxed': 0.30, 'mixed': 0.60,
                    'hard': 0.80, 'brutal': 1.00}
        for mode in gunnery.SKILL_MODES:
            self.assertAlmostEqual(
                expected[mode], gunnery._mode_mean(mode), places=6, msg=mode)

    def test_the_pub_default_is_the_widest_spread(self):
        """One mixed roster has to hold a hopeless Bot and a dangerous one."""
        spans = {}
        for mode in gunnery.SKILL_MODES:
            points = gunnery._MODE_RATING_POINTS[mode]
            spans[mode] = points[-1] - points[0]
        self.assertEqual('mixed', max(spans, key=lambda mode: spans[mode]))
        self.assertEqual(0.0, spans['brutal'])


class SkillResolutionTests(unittest.TestCase):
    def _roster(self, mode, round_id=5):
        return [gunnery.resolve_skill(mode, round_id, team, slot)
                for team in (1, 2) for slot in range(15)]

    def test_one_slot_resolves_the_same_way_everywhere(self):
        first = gunnery.resolve_skill('mixed', 9, 2, 4)
        self.assertEqual(first, gunnery.resolve_skill('mixed', 9, 2, 4))
        self.assertIn(first, gunnery.SKILL_TIERS)

    def test_the_brutal_preset_is_every_bot_elite(self):
        self.assertEqual(
            set([gunnery.SKILL_ELITE]), set(self._roster('brutal')))

    def test_the_easy_preset_never_produces_a_strong_gunner(self):
        for round_id in range(1, 40):
            roster = self._roster('easy', round_id)
            self.assertEqual(
                set(), set(roster) & set(
                    (gunnery.SKILL_VETERAN, gunnery.SKILL_ELITE)))

    def test_the_pub_mix_spreads_over_the_whole_ladder(self):
        seen = set()
        for round_id in range(1, 20):
            seen.update(self._roster('mixed', round_id))
        self.assertEqual(set(gunnery.SKILL_TIERS), seen)

    def test_a_preset_samples_the_mean_rating_it_specifies(self):
        for mode in gunnery.SKILL_MODES:
            drawn = [gunnery.resolve_rating(mode, round_id, team, slot)
                     for round_id in range(400)
                     for team in (1, 2) for slot in range(15)]
            self.assertAlmostEqual(
                gunnery._mode_mean(mode), sum(drawn) / float(len(drawn)),
                delta=0.01, msg=mode)
            self.assertTrue(all(0.0 <= value <= 1.0 for value in drawn), mode)

    def test_an_unknown_preset_still_resolves_a_supported_tier(self):
        self.assertIn(
            gunnery.resolve_skill('impossible', 3, 1, 0), gunnery.SKILL_TIERS)


class EngagementErrorTests(unittest.TestCase):
    def test_the_same_engagement_epoch_reproduces_the_same_gunner(self):
        first = gunnery.engagement_error(REGULAR, 5, 11, ('human', 2), 3)
        second = gunnery.engagement_error(REGULAR, 5, 11, ('human', 2), 3)
        self.assertEqual(first, second)

    def test_a_new_epoch_re_lays_the_gun(self):
        errors = [gunnery.engagement_error(REGULAR, 5, 11, ('human', 2), n)
                  for n in range(6)]
        self.assertGreater(len(set(error['radius'] for error in errors)), 1)

    def test_the_radius_stays_inside_the_tier_envelope(self):
        for epoch in range(500):
            error = gunnery.engagement_error(
                ROOKIE, 5, 11, ('bot', 12), epoch)
            self.assertGreaterEqual(error['radius'], 0.0)
            self.assertLessEqual(error['radius'], 1.0)
            self.assertGreaterEqual(error['azimuth'], 0.0)

    def test_the_lead_error_is_bounded_by_the_rating(self):
        for skill in gunnery.SKILL_TIERS:
            rating = gunnery.rating_for_skill(skill)
            bound = gunnery.rating_parameters(rating)['lead_error']
            for epoch in range(200):
                scale = gunnery.engagement_error(
                    rating, 5, 11, ('human', 2), epoch)['lead_scale']
                self.assertGreaterEqual(scale, 1.0 - bound - 1e-9)
                self.assertLessEqual(scale, 1.0 + bound + 1e-9)

    def test_the_aim_offset_grows_with_range_and_with_the_gun_circle(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        near = gunnery.aim_offset_metres(ROOKIE, error, 0.0035, 100.0)
        far = gunnery.aim_offset_metres(ROOKIE, error, 0.0035, 200.0)
        wide = gunnery.aim_offset_metres(ROOKIE, error, 0.0070, 100.0)
        self.assertAlmostEqual(2.0 * near[0], far[0], places=6)
        self.assertAlmostEqual(2.0 * near[0], wide[0], places=6)

    def test_a_better_gunner_lays_the_gun_closer_to_the_centre(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        offsets = [abs(gunnery.aim_offset_metres(
            gunnery.rating_for_skill(skill), error, 0.0035, 300.0)[0])
            for skill in gunnery.SKILL_TIERS]
        self.assertEqual(sorted(offsets, reverse=True), offsets)

    def test_a_long_shot_never_aims_at_the_sky(self):
        error = {'radius': 1.0, 'azimuth': 0.5 * 3.14159265}
        lateral, vertical = gunnery.aim_offset_metres(
            ROOKIE, error, 0.0035, 100000.0)
        self.assertLessEqual(abs(lateral), gunnery.MAX_AIM_OFFSET_METRES)
        self.assertLessEqual(
            abs(vertical),
            gunnery.MAX_AIM_OFFSET_METRES * gunnery.VERTICAL_BIAS_SHARE)

    def test_a_missing_gun_circle_removes_the_bias_instead_of_raising(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        self.assertEqual(
            (0.0, 0.0), gunnery.aim_offset_metres(ROOKIE, error, 0.0, 100.0))
        self.assertEqual(
            (0.0, 0.0),
            gunnery.aim_offset_metres(ROOKIE, error, 0.0035, 0.0))
        self.assertEqual(
            (0.0, 0.0), gunnery.aim_offset_metres(ROOKIE, {}, 0.0035, 100.0))


class FireGateTests(unittest.TestCase):
    def test_no_gunner_fires_before_it_has_reacted(self):
        for rating in (ROOKIE, REGULAR, VETERAN, ELITE, 0.2, 0.55, 0.9):
            reaction = gunnery.rating_parameters(rating)['reaction_seconds']
            self.assertFalse(gunnery.may_fire(rating, reaction - 0.01, 99.0,
                                              1.0))
            self.assertTrue(gunnery.may_fire(rating, reaction, 0.0, 1.0))

    def test_a_converged_circle_fires_without_spending_the_patience(self):
        params = gunnery.rating_parameters(VETERAN)
        self.assertTrue(gunnery.may_fire(
            VETERAN, params['reaction_seconds'], 0.0,
            params['converged_factor']))
        self.assertFalse(gunnery.may_fire(
            VETERAN, params['reaction_seconds'], 0.0,
            params['converged_factor'] + 0.01))

    def test_no_gunner_opens_fire_later_than_its_own_patience(self):
        for rating in (ROOKIE, REGULAR, VETERAN, ELITE, 0.2, 0.55, 0.9):
            params = gunnery.rating_parameters(rating)
            self.assertTrue(gunnery.may_fire(
                rating,
                params['reaction_seconds'] + params['patience_seconds'],
                params['patience_seconds'], float('inf')))

    def test_a_moving_bot_still_fires_once_its_patience_expires(self):
        params = gunnery.rating_parameters(ELITE)
        self.assertFalse(gunnery.may_fire(
            ELITE, 30.0, params['patience_seconds'] - 0.01, 6.0))
        self.assertTrue(gunnery.may_fire(
            ELITE, 30.0, params['patience_seconds'], 6.0))

    def test_only_the_opening_shot_waits_for_the_aiming_circle(self):
        """After the first shot the gun's own reload owns the cadence."""
        self.assertFalse(gunnery.may_fire(ELITE, 300.0, 0.0, 6.0))
        self.assertTrue(
            gunnery.may_fire(ELITE, 300.0, 0.0, 6.0, opening_shot=False))

    def test_a_better_gunner_never_fires_a_follow_up_later_than_a_worse_one(
            self):
        """The spectrum must not invert on a fast or clip gun."""
        ratings = [index / 20.0 for index in range(21)]
        for bloom in (1.0, 2.0, 4.2, float('inf')):
            for laying in (0.0, 0.3, 1.0, 3.0):
                allowed = [gunnery.may_fire(rating, 300.0, laying, bloom,
                                            opening_shot=False)
                           for rating in ratings]
                self.assertEqual(
                    sorted(allowed), allowed,
                    'bloom=%r laying=%r' % (bloom, laying))

    def test_an_unusable_clock_holds_fire_instead_of_guessing(self):
        self.assertFalse(gunnery.may_fire(REGULAR, float('nan'), 0.0, 1.0))
        self.assertFalse(gunnery.may_fire(REGULAR, 5.0, 0.0, float('nan')))
        self.assertFalse(gunnery.may_fire(REGULAR, None, 0.0, 1.0))

    def test_an_infinite_circle_waits_for_the_patience_bound(self):
        params = gunnery.rating_parameters(REGULAR)
        self.assertFalse(gunnery.may_fire(
            REGULAR, 30.0, params['patience_seconds'] - 0.01, float('inf')))
        self.assertTrue(gunnery.may_fire(
            REGULAR, 30.0, params['patience_seconds'], float('inf')))


class RatingSpectrumTests(unittest.TestCase):
    """The tiers survive as anchors on one continuous competence axis."""

    NAMES = ('reaction_seconds', 'patience_seconds', 'converged_factor',
             'aim_bias_factor', 'lead_error', 'crew_level')

    def test_every_anchor_reproduces_its_reviewed_bundle_exactly(self):
        """The reviewed calibration must survive becoming an anchor.

        Anchors are not evenly spaced and float interpolation does not have
        to land on an endpoint, so this is a real property, not a tautology.
        """
        for skill in gunnery.SKILL_TIERS:
            reviewed = gunnery._PARAMETERS[skill]
            interpolated = gunnery.rating_parameters(
                gunnery.rating_for_skill(skill))
            for name in self.NAMES:
                self.assertEqual(
                    reviewed[name], interpolated[name],
                    '%s %s' % (skill, name))

    def test_every_gunnery_knob_moves_one_way_across_the_spectrum(self):
        ratings = [index / 50.0 for index in range(51)]
        rows = [gunnery.rating_parameters(rating) for rating in ratings]
        for name in ('reaction_seconds', 'converged_factor',
                     'aim_bias_factor', 'lead_error'):
            values = [row[name] for row in rows]
            self.assertEqual(sorted(values, reverse=True), values, name)
        for name in ('patience_seconds',):
            values = [row[name] for row in rows]
            self.assertEqual(sorted(values), values, name)
        levels = [row['crew_level'] for row in rows]
        self.assertEqual(sorted(levels), levels)

    def test_a_crew_level_is_never_one_this_project_has_not_seen(self):
        """An unproven level silently degrades to a full default crew."""
        for index in range(101):
            level = gunnery.rating_crew_level(index / 100.0)
            self.assertIn(level, gunnery.PROVEN_CREW_LEVELS)

    def test_the_anchor_crew_levels_are_unchanged(self):
        self.assertEqual(
            [75, 90, 100, 100],
            [gunnery.crew_level(skill) for skill in gunnery.SKILL_TIERS])

    def test_a_label_never_claims_more_competence_than_the_rating(self):
        for index in range(201):
            rating = index / 200.0
            label = gunnery.skill_for_rating(rating)
            anchor = gunnery.rating_for_skill(label)
            self.assertLessEqual(
                anchor - rating, 0.17 + 1e-9, 'rating=%r' % (rating,))

    def test_an_anchor_labels_itself(self):
        for skill in gunnery.SKILL_TIERS:
            self.assertEqual(skill, gunnery.skill_for_rating(
                gunnery.rating_for_skill(skill)))

    def test_an_unusable_rating_becomes_the_default_instead_of_raising(self):
        for value in (None, '', 'elite', float('nan'), float('inf'),
                      object()):
            self.assertEqual(gunnery.DEFAULT_RATING,
                             gunnery.normalize_rating(value))
        self.assertEqual(0.0, gunnery.normalize_rating(-3.0))
        self.assertEqual(1.0, gunnery.normalize_rating(9.0))

    def test_one_slot_resolves_the_same_rating_everywhere(self):
        first = gunnery.resolve_rating('mixed', 9, 2, 4)
        self.assertEqual(first, gunnery.resolve_rating('mixed', 9, 2, 4))

    def test_the_easy_preset_never_draws_a_competent_bot(self):
        ceiling = gunnery._MODE_RATING_POINTS['easy'][-1]
        for round_id in range(60):
            for team in (1, 2):
                for slot in range(15):
                    self.assertLessEqual(
                        gunnery.resolve_rating('easy', round_id, team, slot),
                        ceiling)

    def test_the_brutal_preset_draws_a_complete_bot_every_time(self):
        for round_id in range(20):
            for slot in range(15):
                self.assertEqual(
                    1.0, gunnery.resolve_rating('brutal', round_id, 1, slot))


class CapabilityTests(unittest.TestCase):
    """The rating is also the chance of doing the right thing once."""

    def test_the_ends_of_the_spectrum_are_honest(self):
        for capability in gunnery.CAPABILITIES:
            self.assertFalse(gunnery.capability_allowed(
                0.0, capability, 7, 11, 'occasion'), capability)
            self.assertTrue(gunnery.capability_allowed(
                1.0, capability, 7, 11, 'occasion'), capability)

    def test_one_occasion_reaches_the_same_answer_every_time(self):
        """A planner rebuilds an order every tick and after a failover."""
        for rating in (0.2, 0.5, 0.8):
            first = gunnery.capability_allowed(
                rating, gunnery.CAPABILITY_TACTICAL_COVER, 4, 9, 'bot', 3)
            for unused_repeat in range(5):
                self.assertEqual(first, gunnery.capability_allowed(
                    rating, gunnery.CAPABILITY_TACTICAL_COVER, 4, 9,
                    'bot', 3))

    def test_a_new_occasion_is_a_new_decision(self):
        answers = set(
            gunnery.capability_allowed(
                0.5, gunnery.CAPABILITY_TACTICAL_COVER, 4, 9, 'bot', index)
            for index in range(40))
        self.assertEqual(set((True, False)), answers)

    def test_two_capabilities_never_share_one_draw(self):
        """Otherwise a Bot would know everything or nothing at 0.5."""
        for rating in (0.35, 0.5, 0.65):
            answers = set(
                gunnery.capability_allowed(rating, capability, 4, 9, 'x')
                for capability in gunnery.CAPABILITIES)
            self.assertEqual(set((True, False)), answers, rating)

    def test_a_rating_succeeds_at_about_its_own_rate(self):
        for rating in (0.1, 0.3, 0.6, 0.8):
            hits = sum(
                1 for index in range(4000)
                if gunnery.capability_allowed(
                    rating, gunnery.CAPABILITY_COVER_PEEK, 1, index))
            self.assertAlmostEqual(
                rating, hits / 4000.0, delta=0.025, msg=rating)

    def test_competence_never_costs_a_bot_a_capability(self):
        """More competence must never mean fewer right decisions."""
        occasions = [(1, index) for index in range(600)]
        counts = []
        for index in range(11):
            rating = index / 10.0
            counts.append(sum(
                1 for occasion in occasions
                if gunnery.capability_allowed(
                    rating, gunnery.CAPABILITY_FLANK, *occasion)))
        self.assertEqual(sorted(counts), counts)

    def test_the_latched_capabilities_are_real_capabilities(self):
        self.assertTrue(gunnery.LATCHED_CAPABILITIES)
        for capability in gunnery.LATCHED_CAPABILITIES:
            self.assertIn(capability, gunnery.CAPABILITIES)

    def test_the_exponent_is_the_reviewed_product_choice(self):
        """One knob answers "the tactical half bites harder than gunnery"."""
        self.assertEqual(1.0, gunnery.CAPABILITY_EXPONENT)


class SkillVocabularyParityTests(unittest.TestCase):
    """One vocabulary, four owners: worker, wire, room panel and launcher."""

    def test_the_launcher_offers_the_same_tiers_as_the_worker(self):
        import bot_lineup_profiles

        self.assertEqual(
            tuple(gunnery.SKILL_TIERS),
            tuple(bot_lineup_profiles.BOT_SKILL_TIERS_0922))

    def test_the_waiting_room_offers_every_preset_in_order(self):
        waiting_room_ui = _load('waiting_room_ui')

        self.assertEqual(
            tuple(gunnery.SKILL_MODES),
            tuple(value for value, unused_label
                  in waiting_room_ui.BOT_SKILL_OPTIONS))

    def test_the_wire_accepts_exactly_the_supported_presets(self):
        lan_client = _load('lan_client')

        self.assertEqual(
            frozenset(gunnery.SKILL_MODES), lan_client.BOT_SKILL_MODES)


if __name__ == '__main__':
    unittest.main()
