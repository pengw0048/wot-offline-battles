"""Regressions for the report 20260924-102359, without engine or user saves."""
import collections
import copy
import hashlib
import json
import random
import unittest

import test_port_0922_bot_state_codec as cf
from gui.mods.offline_lan_0922.ai import planner


def profile(level, role):
    return {'name': 'test:%s_%d' % (role, level), 'level': level, 'tags': [role]}


def signature(rows):
    return collections.Counter((row['level'], planner.vehicle_match_class(row))
                               for row in rows)


class MatchmakingRepairTests(unittest.TestCase):
    def setUp(self):
        self.pool = [profile(level, role) for level in (6, 7, 8)
                     for role in planner.MATCH_CLASSES]
        self.human = profile(7, 'mediumTank')

    def test_real_fifteen_tier_templates(self):
        for tiers, expected in (((6, 7, 8), {6: 7, 7: 5, 8: 3}),
                                ((7, 8), {7: 10, 8: 5}), ((7,), {7: 15})):
            for seed in range(40):
                rows = planner.build_match_template(
                    self.pool, 15, self.human, tiers, random.Random(seed))
                self.assertEqual(expected, dict(collections.Counter(
                    row['level'] for row in rows)))

    def test_zero_through_three_spgs_are_all_reachable(self):
        observed = set()
        for seed in range(128):
            rows = planner.build_match_template(
                self.pool, 15, self.human, (6, 7, 8), random.Random(seed))
            observed.add(sum(planner.vehicle_match_class(row) == 'SPG'
                             for row in rows))
        self.assertEqual({0, 1, 2, 3}, observed)

    def test_human_spgs_consume_the_same_team_quota(self):
        for humans in range(1, 4):
            required = [profile(7, 'SPG')] * humans
            for seed in range(32):
                rows = planner.build_match_template(
                    self.pool, 15, required[0], (6, 7, 8),
                    random.Random(seed), required_profiles=required)
                remaining = planner.remaining_match_template(rows, required)
                bots = planner.select_bot_lineup(
                    remaining, 15-humans, spg_limit=3-humans,
                    fallback_candidates=self.pool)
                count = humans + sum(planner.vehicle_match_class(row) == 'SPG'
                                     for row in bots)
                self.assertLessEqual(count, 3)
                self.assertEqual(15-humans, len(bots))

    def test_spg_present_at_one_tier_is_not_discarded_by_other_tier_slots(self):
        pool = [p for p in self.pool if planner.vehicle_match_class(p) != 'SPG'
                or p['level'] == 7]
        counts = set()
        for seed in range(80):
            rows = planner.build_match_template(
                pool, 15, self.human, (6, 7, 8), random.Random(seed))
            counts.add(sum(planner.vehicle_match_class(p) == 'SPG' for p in rows))
            self.assertEqual({6: 7, 7: 5, 8: 3}, dict(collections.Counter(
                row['level'] for row in rows)))
        self.assertEqual({0, 1, 2, 3}, counts)

    def test_missing_artillery_assets_never_reduce_a_normal_team(self):
        pool = [p for p in self.pool if planner.vehicle_match_class(p) != 'SPG']
        for seed in range(32):
            rows = planner.build_match_template(
                pool, 15, self.human, (6, 7, 8), random.Random(seed))
            self.assertEqual(15, len(rows))
            self.assertTrue(all(planner.vehicle_match_class(p) != 'SPG' for p in rows))

    def test_mirrored_slots_include_different_human_vehicles(self):
        humans = {1: [profile(6, 'mediumTank'), profile(7, 'SPG')],
                  2: [profile(8, 'heavyTank')]}
        required = planner.shared_human_requirements(humans)
        for seed in range(32):
            template = planner.build_match_template(
                self.pool, 15, humans[1][0], (6, 7, 8), random.Random(seed), required)
            signatures = []
            for team in (1, 2):
                bots = planner.remaining_match_template(template, humans[team])
                signatures.append(signature(bots + humans[team]))
            self.assertEqual(signatures[0], signatures[1])
            self.assertEqual(15, sum(signatures[0].values()))

    def test_custom_team_scaling_always_preserves_total(self):
        for size in range(1, 16):
            for tiers in ((7,), (7, 8), (6, 7, 8)):
                slots = planner.match_tier_slots(tiers, size)
                self.assertEqual(size, len(slots))
                self.assertTrue(set(slots).issubset(tiers))


class ContactWireRepairTests(unittest.TestCase):
    def test_legacy_row_without_extension_keeps_its_columns(self):
        row = cf.codec.encode_row(cf._bot_state())
        digest = hashlib.sha256(json.dumps(row, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual('15470dd5fdc0a5a25926f937b5cafb4f9d916e7489bf3d0a3ada2d62f65e4017', digest)
        decoded = cf.codec.decode_row(row, cf.STATIC)
        self.assertNotIn('contact_push_acks', decoded)

    def test_momentum_appends_after_legacy_variable_groups(self):
        state = cf._bot_state()
        old = cf.codec.encode_row(state)
        state.update(push_x=-1.25, push_z=2.5,
                     contact_push_acks=[[3, 9, 220000.0, -45000.0, .2, -.4, 0.0]])
        extended = cf.codec.encode_row(state)
        changed = [(a, b) for a, b in zip(old, extended) if a != b]
        self.assertEqual(1, len(changed))  # Only the optional presence bit.
        self.assertEqual(cf.codec.F_HAS_CONTACT_MOMENTUM, changed[0][0] ^ changed[0][1])
        self.assertEqual(11, len(extended)-len(old))
        decoded = cf.codec.decode_row(extended, cf.STATIC)
        for field in ('push_x', 'push_z', 'contact_push_acks'):
            self.assertEqual(state[field], decoded[field])
        self.assertEqual(extended, cf.codec.encode_row(decoded))

    def test_truncated_or_duplicate_acknowledgements_are_rejected(self):
        state = cf._bot_state(contact_push_acks=[[3, 9, 220000.0, 0.0, .2, -.4, 0.0]])
        row = cf.codec.encode_row(state)
        for end in range(1, 10):
            with self.subTest(end=end), self.assertRaises(cf.codec.BotStateCodecError):
                cf.codec.decode_row(row[:-end], cf.STATIC)
        state['contact_push_acks'] *= 2
        with self.assertRaises(cf.codec.BotStateCodecError):
            cf.codec.encode_row(state)

    def test_wire_projection_does_not_mutate_checkpoints(self):
        state = cf._bot_state(contact_push_acks=[[3, 9, 220000.0, 0.0, .2, -.4, 0.0]])
        before = copy.deepcopy(state)
        cf.codec.decode_row(cf.codec.encode_row(state), cf.STATIC)
        self.assertEqual(before, state)
