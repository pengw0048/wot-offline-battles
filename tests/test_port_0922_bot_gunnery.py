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

    def test_every_preset_is_a_complete_distribution(self):
        for mode in gunnery.SKILL_MODES:
            weights = gunnery._MODE_WEIGHTS[mode]
            self.assertEqual(len(gunnery.SKILL_TIERS), len(weights), mode)
            self.assertAlmostEqual(1.0, sum(weights), places=6, msg=mode)
            self.assertTrue(all(weight >= 0.0 for weight in weights), mode)


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

    def test_a_preset_roughly_matches_its_own_weights(self):
        counts = dict((skill, 0) for skill in gunnery.SKILL_TIERS)
        rounds = 400
        for round_id in range(rounds):
            for skill in self._roster('mixed', round_id):
                counts[skill] += 1
        total = float(sum(counts.values()))
        for index, skill in enumerate(gunnery.SKILL_TIERS):
            expected = gunnery._MODE_WEIGHTS['mixed'][index]
            self.assertAlmostEqual(
                expected, counts[skill] / total, delta=0.03, msg=skill)

    def test_an_unknown_preset_still_resolves_a_supported_tier(self):
        self.assertIn(
            gunnery.resolve_skill('impossible', 3, 1, 0), gunnery.SKILL_TIERS)


class EngagementErrorTests(unittest.TestCase):
    def test_the_same_engagement_epoch_reproduces_the_same_gunner(self):
        first = gunnery.engagement_error('regular', 5, 11, ('human', 2), 3)
        second = gunnery.engagement_error('regular', 5, 11, ('human', 2), 3)
        self.assertEqual(first, second)

    def test_a_new_epoch_re_lays_the_gun(self):
        errors = [gunnery.engagement_error('regular', 5, 11, ('human', 2), n)
                  for n in range(6)]
        self.assertGreater(len(set(error['radius'] for error in errors)), 1)

    def test_the_radius_stays_inside_the_tier_envelope(self):
        for epoch in range(500):
            error = gunnery.engagement_error(
                'rookie', 5, 11, ('bot', 12), epoch)
            self.assertGreaterEqual(error['radius'], 0.0)
            self.assertLessEqual(error['radius'], 1.0)
            self.assertGreaterEqual(error['azimuth'], 0.0)

    def test_the_lead_error_is_bounded_by_the_tier(self):
        for skill in gunnery.SKILL_TIERS:
            bound = gunnery.skill_parameters(skill)['lead_error']
            for epoch in range(200):
                scale = gunnery.engagement_error(
                    skill, 5, 11, ('human', 2), epoch)['lead_scale']
                self.assertGreaterEqual(scale, 1.0 - bound - 1e-9)
                self.assertLessEqual(scale, 1.0 + bound + 1e-9)

    def test_the_aim_offset_grows_with_range_and_with_the_gun_circle(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        near = gunnery.aim_offset_metres('rookie', error, 0.0035, 100.0)
        far = gunnery.aim_offset_metres('rookie', error, 0.0035, 200.0)
        wide = gunnery.aim_offset_metres('rookie', error, 0.0070, 100.0)
        self.assertAlmostEqual(2.0 * near[0], far[0], places=6)
        self.assertAlmostEqual(2.0 * near[0], wide[0], places=6)

    def test_a_better_gunner_lays_the_gun_closer_to_the_centre(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        offsets = [abs(gunnery.aim_offset_metres(
            skill, error, 0.0035, 300.0)[0]) for skill in gunnery.SKILL_TIERS]
        self.assertEqual(sorted(offsets, reverse=True), offsets)

    def test_a_long_shot_never_aims_at_the_sky(self):
        error = {'radius': 1.0, 'azimuth': 0.5 * 3.14159265}
        lateral, vertical = gunnery.aim_offset_metres(
            'rookie', error, 0.0035, 100000.0)
        self.assertLessEqual(abs(lateral), gunnery.MAX_AIM_OFFSET_METRES)
        self.assertLessEqual(
            abs(vertical),
            gunnery.MAX_AIM_OFFSET_METRES * gunnery.VERTICAL_BIAS_SHARE)

    def test_a_missing_gun_circle_removes_the_bias_instead_of_raising(self):
        error = {'radius': 1.0, 'azimuth': 0.0}
        self.assertEqual(
            (0.0, 0.0), gunnery.aim_offset_metres('rookie', error, 0.0, 100.0))
        self.assertEqual(
            (0.0, 0.0),
            gunnery.aim_offset_metres('rookie', error, 0.0035, 0.0))
        self.assertEqual(
            (0.0, 0.0), gunnery.aim_offset_metres('rookie', {}, 0.0035, 100.0))


class FireGateTests(unittest.TestCase):
    def test_no_tier_fires_before_it_has_reacted(self):
        for skill in gunnery.SKILL_TIERS:
            reaction = gunnery.skill_parameters(skill)['reaction_seconds']
            self.assertFalse(gunnery.may_fire(skill, reaction - 0.01, 99.0,
                                              1.0))
            self.assertTrue(gunnery.may_fire(skill, reaction, 0.0, 1.0))

    def test_a_converged_circle_fires_without_spending_the_patience(self):
        params = gunnery.skill_parameters('veteran')
        self.assertTrue(gunnery.may_fire(
            'veteran', params['reaction_seconds'], 0.0,
            params['converged_factor']))
        self.assertFalse(gunnery.may_fire(
            'veteran', params['reaction_seconds'], 0.0,
            params['converged_factor'] + 0.01))

    def test_a_better_tier_never_opens_fire_later_than_its_own_patience(self):
        for skill in gunnery.SKILL_TIERS:
            params = gunnery.skill_parameters(skill)
            self.assertTrue(gunnery.may_fire(
                skill,
                params['reaction_seconds'] + params['patience_seconds'],
                params['patience_seconds'], float('inf')))

    def test_a_moving_bot_still_fires_once_its_patience_expires(self):
        params = gunnery.skill_parameters('elite')
        self.assertFalse(gunnery.may_fire(
            'elite', 30.0, params['patience_seconds'] - 0.01, 6.0))
        self.assertTrue(gunnery.may_fire(
            'elite', 30.0, params['patience_seconds'], 6.0))

    def test_only_the_opening_shot_waits_for_the_aiming_circle(self):
        """After the first shot the gun's own reload owns the cadence."""
        self.assertFalse(gunnery.may_fire('elite', 300.0, 0.0, 6.0))
        self.assertTrue(
            gunnery.may_fire('elite', 300.0, 0.0, 6.0, opening_shot=False))

    def test_a_better_tier_never_fires_a_follow_up_later_than_a_worse_one(self):
        """The ladder must not invert on a fast or clip gun."""
        for bloom in (1.0, 2.0, 4.2, float('inf')):
            for laying in (0.0, 0.3, 1.0, 3.0):
                allowed = [gunnery.may_fire(skill, 300.0, laying, bloom,
                                            opening_shot=False)
                           for skill in gunnery.SKILL_TIERS]
                self.assertEqual(
                    sorted(allowed), allowed,
                    'bloom=%r laying=%r' % (bloom, laying))

    def test_an_unusable_clock_holds_fire_instead_of_guessing(self):
        self.assertFalse(gunnery.may_fire('regular', float('nan'), 0.0, 1.0))
        self.assertFalse(gunnery.may_fire('regular', 5.0, 0.0, float('nan')))
        self.assertFalse(gunnery.may_fire('regular', None, 0.0, 1.0))

    def test_an_infinite_circle_waits_for_the_patience_bound(self):
        params = gunnery.skill_parameters('regular')
        self.assertFalse(gunnery.may_fire(
            'regular', 30.0, params['patience_seconds'] - 0.01, float('inf')))
        self.assertTrue(gunnery.may_fire(
            'regular', 30.0, params['patience_seconds'], float('inf')))


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
