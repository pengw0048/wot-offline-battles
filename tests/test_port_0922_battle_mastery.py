"""Mastery badge and Marks of Excellence contract tests.

The numbers here are the shipped retail table rows, not invented fixtures:
Type 59 is compact descriptor 49, and the literals below are what
``tools/bake_mastery_thresholds_0922.py`` captured for it.  Pinning them means
a re-bake that moves Type 59's bar fails this suite instead of passing
silently.
"""

import json
import sys
import tempfile
from unittest import mock
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
sys.path.insert(0, str(ROOT / 'tools'))

import bake_mastery_thresholds_0922 as mastery_baker

from gui.mods.offline_lan_0922 import battle_mastery, mastery_catalog
from gui.mods.offline_lan_0922.account_rpc import data, postbattle_store


TYPE_59 = 49
TYPE_59_TAGS = ('mediumTank',)
TYPE_59_TIER = 8
# The captured retail row: base XP for classes III, II, I and Ace.
TYPE_59_MASTERY_XP = (597, 875, 1144, 1384)
# The captured retail combined damage for one, two and three marks.
TYPE_59_MARKS = (1449, 2118, 2659)
MARKS_DB_ID = 295


class _Packer(object):
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def pack(self, value):
        self.calls.append((self.name, dict(value)))
        return [self.name, dict(value)]


class _Packers(object):
    def __init__(self):
        self.calls = []
        for name in ('AVATAR_FULL_RESULTS', 'VEH_FULL_RESULTS',
                     'COMMON_RESULTS', 'PLAYER_INFO', 'VEH_PUBLIC_RESULTS',
                     'AVATAR_PUBLIC_RESULTS'):
            setattr(self, name, _Packer(name, self.calls))

    def vehicle(self):
        for name, value in reversed(self.calls):
            if name == 'VEH_FULL_RESULTS':
                return value
        raise AssertionError('the personal result was never packed')


class _ReplayConnector(object):
    def __init__(self, unused_packer, values):
        self.values = values


class _Replay(object):
    """The #1513 chain, as far as ``_add_value_replays`` uses it.

    ``ValueReplay`` writes the running total back through the connector on the
    initial value and on every later step, and ``ReplayRecords`` keys each
    record by the name of the value that step applied.  Both are the contract
    the results tables read, so the double reproduces them.
    """

    steps = []

    def __init__(self, connector, recordName=None, startRecordName=None):
        self.connector = connector
        self.record_name = recordName
        self.start_name = startRecordName
        self.chain = ['SET:%s' % startRecordName]
        self.connector.values[recordName] = self.connector.values[
            startRecordName]
        _Replay.steps.append((recordName, 'SET', startRecordName))

    def __mul__(self, other):
        # ``__opMul`` is ``int(round(value * factor / 100.0))`` under the
        # embedded CPython 2.7, which rounds a half away from zero.
        self.connector.values[self.record_name] = int(
            self.connector.values[self.record_name] *
            self.connector.values[other] / 100.0 + 0.5)
        self.chain.append('MUL:%s' % other)
        _Replay.steps.append((self.record_name, 'MUL', other))
        return self

    def __add__(self, other):
        self.connector.values[self.record_name] += self.connector.values[
            other]
        self.chain.append('ADD:%s' % other)
        _Replay.steps.append((self.record_name, 'ADD', other))
        return self

    def pack(self):
        return ('%s=%s' % ('+'.join(self.chain),
                           self.connector.values[self.record_name])).encode(
                               'ascii')


def _receipt(account_key='account-key-123456', index=1, xp=600, damage=900,
             assist_track=0, assist_radio=0, assist_stun=0,
             vehicle='china:Ch01_Type59'):
    stats = {
        'shots': 8, 'direct_hits': 6, 'piercings': 4,
        'damage': damage, 'damage_received': 300, 'damage_blocked': 100,
        'assist_track': assist_track, 'assist_radio': assist_radio,
        'assist_stun': assist_stun,
        'kills': 2, 'spotted': 1, 'capture_points': 5,
        'dropped_capture_points': 0,
    }
    receipt = {
        'receipt_id': 'server:7:%d' % index,
        'arena_unique_id': (7 << 32) | index,
        'round_id': 7, 'player_id': 1,
        'account_key': account_key,
        'player_name': 'Alice', 'vehicle': vehicle,
        'team': 1, 'winner': 1, 'map': '01_karelia',
        'finish_reason': 1, 'death_reason': -1, 'duration': 120,
        'premature_leave': False,
        'stats': stats,
        'rewards': {'credits': 4200, 'xp': xp, 'free_xp': 30,
                    'repair_cost': 0, 'ammo_cost': 0},
        'public_results': [{
            'actor_kind': 'player', 'actor_id': 1, 'name': 'Alice',
            'vehicle': vehicle, 'team': 1, 'health': 100,
            'death_reason': -1, 'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': xp,
            'stats': dict(stats),
        }],
    }
    return receipt


class MasteryDecisionTests(unittest.TestCase):
    def test_baker_keeps_playable_secret_vehicles_but_excludes_helpers(self):
        roster = {
            62977: ('ussr', 'R127_T44_100_P', 8,
                    ['mediumTank', 'secret', 'telecom']),
            1: ('ussr', 'retired_tank', 8,
                ['mediumTank', 'secret', 'unrecoverable']),
            2: ('ussr', 'tank_bootcamp', 2, ['mediumTank', 'secret']),
            3: ('ussr', 'tank_bot', 8, ['mediumTank', 'secret']),
            4: ('ussr', 'tank_IGR', 8, ['mediumTank', 'premiumIGR']),
            5: ('ussr', 'Observer', 1, ['observer', 'secret']),
            6: ('ussr', 'event_tank', 8, ['mediumTank', 'fallout']),
        }
        self.assertEqual({62977, 1}, set(
            mastery_baker.standard_battle_roster(roster)))

    def test_shipped_secret_variants_resolve_fallback_without_overrides(self):
        for intcd in (62977, 48897):
            self.assertEqual((8, 'mediumTank'),
                             battle_mastery.vehicle_profile(intcd))
            self.assertNotIn(intcd, mastery_catalog.MARKS_DAMAGE)
            self.assertEqual(
                mastery_catalog.MARKS_DAMAGE_FALLBACK[(8, 'mediumTank')],
                battle_mastery.marks_curve(intcd))
            self.assertEqual(
                mastery_catalog.MASTERY_XP_FALLBACK[(8, 'mediumTank')],
                battle_mastery.mastery_thresholds(intcd))

    def test_combined_damage_takes_the_largest_assist_not_the_sum(self):
        # Wargaming's rule: "The maximum damage caused by destroying a track,
        # spotting, or stunning is counted - not the sum of these values."
        self.assertEqual(1400, battle_mastery.combined_damage({
            'damage': 1000, 'assist_track': 400, 'assist_radio': 300,
            'assist_stun': 100}))
        self.assertEqual(1000, battle_mastery.combined_damage(
            {'damage': 1000}))
        self.assertEqual(0, battle_mastery.combined_damage(None))

    def test_the_shipped_table_still_holds_the_captured_retail_row(self):
        self.assertEqual(TYPE_59_MASTERY_XP,
                         mastery_catalog.MASTERY_XP[TYPE_59])
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        self.assertEqual(TYPE_59_MARKS, tuple(
            curve[mastery_catalog.MARKS_PERCENTILES.index(percentile)]
            for percentile in battle_mastery.MARK_PERCENTILES))
        self.assertEqual((TYPE_59_TIER, TYPE_59_TAGS[0]),
                         mastery_catalog.VEHICLE_PROFILE[TYPE_59])

    def test_mastery_class_boundaries_come_from_the_retail_row(self):
        thresholds = battle_mastery.mastery_thresholds(TYPE_59)
        self.assertEqual(TYPE_59_MASTERY_XP, thresholds)
        third, second, first, ace = thresholds
        for base_xp, expected in ((third - 1, 0), (third, 1), (second, 2),
                                  (first, 3), (ace, 4), (ace * 3, 4)):
            self.assertEqual(
                expected, battle_mastery.mark_of_mastery(base_xp, thresholds),
                'base XP %d' % base_xp)

    def test_no_retail_row_awards_no_mastery(self):
        self.assertIsNone(battle_mastery.mastery_thresholds(0))
        self.assertEqual(
            0, battle_mastery.mark_of_mastery(9999, None))

    def test_absent_vehicle_falls_back_to_its_tier_and_class_median(self):
        # A Chinese-server exclusive has no retail row of its own.
        absent = 0xffff01
        self.assertNotIn(absent, mastery_catalog.MASTERY_XP)
        self.assertNotIn(absent, mastery_catalog.VEHICLE_PROFILE)
        expected = mastery_catalog.MASTERY_XP_FALLBACK[(8, 'heavyTank')]
        self.assertEqual(expected, battle_mastery.mastery_thresholds(
            absent, tier=8, tags=('heavyTank', 'heavyTank1')))
        # Outside the baked roster and without an override there is nothing to
        # fall back to.
        self.assertIsNone(battle_mastery.mastery_thresholds(absent))
        # A vehicle the client ships without a retail row of its own resolves
        # its tier and class from the baked roster alone.
        nameless = [intcd for intcd, profile
                    in mastery_catalog.VEHICLE_PROFILE.items()
                    if intcd not in mastery_catalog.MASTERY_XP][0]
        self.assertEqual(
            mastery_catalog.MASTERY_XP_FALLBACK[
                mastery_catalog.VEHICLE_PROFILE[nameless]],
            battle_mastery.mastery_thresholds(nameless))

    def test_marks_need_tier_five_and_the_retail_percentiles(self):
        self.assertEqual((TYPE_59_TIER, TYPE_59_TAGS[0]),
                         battle_mastery.vehicle_profile(TYPE_59))
        curve = battle_mastery.marks_curve(TYPE_59)
        self.assertEqual(mastery_catalog.MARKS_DAMAGE[TYPE_59], curve)
        self.assertIsNone(battle_mastery.marks_curve(TYPE_59, tier=4))
        low_tier = [intcd for intcd, profile
                    in mastery_catalog.VEHICLE_PROFILE.items()
                    if profile[0] < battle_mastery.MARKS_MIN_TIER][0]
        self.assertIsNone(battle_mastery.marks_curve(low_tier))
        self.assertIsNotNone(battle_mastery.mastery_thresholds(low_tier))
        index = dict((percentile, position) for position, percentile
                     in enumerate(mastery_catalog.MARKS_PERCENTILES))
        for percentile, expected in ((65, 1), (85, 2), (95, 3)):
            threshold = curve[index[percentile]]
            self.assertEqual(TYPE_59_MARKS[expected - 1], threshold)
            self.assertEqual(expected,
                             battle_mastery.marks_on_gun(threshold, curve))
            self.assertEqual(expected - 1,
                             battle_mastery.marks_on_gun(threshold - 1, curve))
        self.assertEqual(0, battle_mastery.marks_on_gun(1, curve))
        self.assertEqual(0, battle_mastery.marks_on_gun(99999, None))

    def test_damage_rating_is_monotonic_and_clamped(self):
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        percentiles = mastery_catalog.MARKS_PERCENTILES
        self.assertEqual(0.0, battle_mastery.damage_rating(0, curve))
        self.assertEqual(0.0, battle_mastery.damage_rating(500, None))
        self.assertEqual(battle_mastery.MAX_DAMAGE_RATING,
                         battle_mastery.damage_rating(curve[-1] * 2, curve))
        # The retail service floors its lowest percentiles: a row repeats one
        # damage value for 5 through 20 percent.  A repeated value is reported
        # as the highest percentile that carries it, and every percentile
        # whose value is distinct is reported exactly.
        for position, percentile in enumerate(percentiles):
            if position + 1 < len(curve) and curve[position + 1] == curve[
                    position]:
                continue
            self.assertAlmostEqual(
                float(percentile),
                battle_mastery.damage_rating(curve[position], curve),
                places=6)
        previous = -1.0
        for average in range(0, curve[-1] + 200, 37):
            rating = battle_mastery.damage_rating(average, curve)
            self.assertGreaterEqual(rating, previous)
            previous = rating
        index = percentiles.index(65)
        middle = (curve[index] + curve[index + 1]) // 2
        self.assertGreater(battle_mastery.damage_rating(middle, curve), 65.0)
        self.assertLess(battle_mastery.damage_rating(middle, curve),
                        float(percentiles[index + 1]))

    def test_the_average_uses_the_retail_smoothing_factor(self):
        # The Marks of Excellence mods compute the next value as
        # ``k * (damage + largest assist) + (1 - k) * movingAvgDamage`` with
        # ``k = 2 / (100 + 1)``.
        self.assertEqual(battle_mastery.MOVING_AVERAGE_BATTLES, 100)
        self.assertAlmostEqual(2.0 / 101.0,
                               battle_mastery.MOVING_AVERAGE_SMOOTHING,
                               places=12)
        self.assertEqual(0, battle_mastery.next_moving_average(0, 0))
        self.assertEqual(
            int(round(2.0 / 101.0 * 3000)),
            battle_mastery.next_moving_average(0, 3000))
        self.assertEqual(
            int(round(2.0 / 101.0 * 3000 + 99.0 / 101.0 * 1000)),
            battle_mastery.next_moving_average(1000, 3000))
        # A vehicle already at its own average stays there.
        self.assertEqual(2000, battle_mastery.next_moving_average(2000, 2000))

    def test_one_battle_cannot_earn_a_mark_however_good_it_was(self):
        """A new vehicle starts at zero, so the first battle is diluted."""
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        three_marks = curve[mastery_catalog.MARKS_PERCENTILES.index(95)]
        awards = battle_mastery.battle_awards(
            0, {'damage': three_marks * 2}, 0, TYPE_59)
        self.assertEqual(0, awards['marksOnGun'])
        self.assertLess(awards['movingAvgDamage'], three_marks // 10)
        self.assertLess(awards['damageRating'], 10.0)

    def test_marks_arrive_only_as_the_average_converges(self):
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        arrived = {}
        average = 0
        for battle in range(1, 501):
            awards = battle_mastery.battle_awards(
                0, {'damage': 3000}, average, TYPE_59)
            average = awards['movingAvgDamage']
            arrived.setdefault(awards['marksOnGun'], battle)
        self.assertEqual([0, 1, 2, 3], sorted(arrived))
        self.assertEqual(1, arrived[0])
        # Sustained damage above the three-mark bar still takes most of a
        # hundred battles to get there, and each mark in order.
        self.assertLess(arrived[1], arrived[2])
        self.assertLess(arrived[2], arrived[3])
        self.assertGreater(arrived[1], 20)
        self.assertGreater(arrived[3], 80)
        self.assertLess(arrived[3], 200)
        # The average converges on the damage itself, never above it.
        self.assertLessEqual(average, 3000)
        self.assertGreater(average, 2900)

    def test_earned_marks_and_mastery_never_regress(self):
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        three_marks = curve[mastery_catalog.MARKS_PERCENTILES.index(95)]
        # Start from a vehicle that has already earned its third mark.
        awards = battle_mastery.battle_awards(
            99999, {'damage': three_marks}, three_marks, TYPE_59)
        self.assertEqual(3, awards['marksOnGun'])
        self.assertEqual(4, awards['markOfMastery'])
        collapsed = battle_mastery.battle_awards(
            1, {'damage': 0}, 0, TYPE_59,
            previous_mastery=awards['bestMarkOfMastery'],
            previous_marks=awards['marksOnGun'])
        self.assertEqual(3, collapsed['marksOnGun'])
        self.assertEqual(0, collapsed['markOfMastery'])
        self.assertEqual(4, collapsed['prevMarkOfMastery'])
        self.assertEqual(4, collapsed['bestMarkOfMastery'])


class MasteryResultTests(unittest.TestCase):
    def setUp(self):
        self._original_vehicle = (
            postbattle_store._vehicle_type_compact_descr)
        self._original_arena = postbattle_store._arena_type_id
        postbattle_store._vehicle_type_compact_descr = lambda unused: TYPE_59
        postbattle_store._arena_type_id = lambda unused: 70001
        self.addCleanup(self._restore)

    def _restore(self):
        postbattle_store._vehicle_type_compact_descr = self._original_vehicle
        postbattle_store._arena_type_id = self._original_arena

    def _result(self, store, receipt):
        packers = _Packers()
        self.assertIsNotNone(store.result(
            receipt['arena_unique_id'], packers=packers,
            replay_types=(_Replay, _ReplayConnector),
            record_db_ids={('achievements', 'marksOnGun'): MARKS_DB_ID}))
        return packers.vehicle()

    def test_secret_vehicle_earns_and_persists_marks_and_mastery(self):
        postbattle_store._vehicle_type_compact_descr = lambda unused: 62977
        name = 'ussr:R127_T44_100_P'
        curve = battle_mastery.marks_curve(62977)
        one_mark = curve[mastery_catalog.MARKS_PERCENTILES.index(65)]
        ace_xp = battle_mastery.mastery_thresholds(62977)[3]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            self.assertTrue(store.accept(_receipt(
                store.account_key, vehicle=name, damage=one_mark)))
            row = store._progress['vehicles'][name]
            self.assertEqual(0, row['marksOnGun'])
            row['movingAvgDamage'] = one_mark - 1
            crossing = _receipt(store.account_key, index=2, vehicle=name,
                                damage=one_mark * 3, xp=ace_xp)
            self.assertTrue(store.accept(crossing))
            result = self._result(store, crossing)
            self.assertEqual(1, result['marksOnGun'])
            self.assertEqual(4, result['markOfMastery'])
            self.assertGreaterEqual(result['damageRating'], 65)
            self.assertIn((MARKS_DB_ID, 1), result['dossierPopUps'])
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(result, self._result(restarted, crossing))

    def test_store_publishes_the_earned_marks_to_the_battle_barrel(self):
        """The account server owns publicInfo['marksOnGun'].

        ``account_rpc.data.dossiers`` publishes this same row value as the
        garage badge, so the barrel decal in battle and the garage badge read
        one number rather than two independently derived ones.
        """
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        one_mark = curve[mastery_catalog.MARKS_PERCENTILES.index(65)]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(0, store.marks_on_gun('china:Ch01_Type59'))
            self.assertTrue(store.accept(_receipt(
                store.account_key, damage=one_mark)))
            self.assertEqual(0, store.marks_on_gun('china:Ch01_Type59'))

            store._progress['vehicles']['china:Ch01_Type59'][
                'movingAvgDamage'] = one_mark - 1
            self.assertTrue(store.accept(_receipt(
                store.account_key, index=2, damage=one_mark * 3)))

            self.assertEqual(1, store.marks_on_gun('china:Ch01_Type59'))
            self.assertEqual(
                1, postbattle_store.PostBattleStore(
                    path=path).marks_on_gun('china:Ch01_Type59'))

    def test_store_reports_no_marks_for_an_unknown_or_damaged_row(self):
        with tempfile.TemporaryDirectory() as folder:
            store = postbattle_store.PostBattleStore(
                path=str(Path(folder) / 'postbattle_state.json'))

            self.assertEqual(0, store.marks_on_gun('ussr:R11_MS-1'))
            store._progress['vehicles']['ussr:R11_MS-1'] = 'not a row'
            self.assertEqual(0, store.marks_on_gun('ussr:R11_MS-1'))
            store._progress['vehicles']['ussr:R11_MS-1'] = {
                'marksOnGun': 'three'}
            self.assertEqual(0, store.marks_on_gun('ussr:R11_MS-1'))
            store._progress['vehicles']['ussr:R11_MS-1'] = {'marksOnGun': 40}
            self.assertEqual(3, store.marks_on_gun('ussr:R11_MS-1'))

    def test_results_carry_the_badge_fields_and_the_new_mark_popup(self):
        curve = mastery_catalog.MARKS_DAMAGE[TYPE_59]
        one_mark = curve[mastery_catalog.MARKS_PERCENTILES.index(65)]
        ace_xp = mastery_catalog.MASTERY_XP[TYPE_59][3]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            # A first battle is diluted by the empty history, so drive the
            # vehicle to just under its first mark and then cross it.  Its XP
            # stays under the third class so the crossing battle is also the
            # first mastery badge.
            receipt = _receipt(
                store.account_key, damage=one_mark,
                xp=mastery_catalog.MASTERY_XP[TYPE_59][0] - 1)
            self.assertTrue(store.accept(receipt))
            row = store._progress['vehicles']['china:Ch01_Type59']
            self.assertEqual(0, row['marksOnGun'])
            row['movingAvgDamage'] = one_mark - 1

            crossing = _receipt(store.account_key, index=2, xp=ace_xp,
                                damage=one_mark * 3)
            self.assertTrue(store.accept(crossing))
            vehicle = self._result(store, crossing)
            self.assertEqual(4, vehicle['markOfMastery'])
            self.assertEqual(0, vehicle['prevMarkOfMastery'])
            self.assertEqual(1, vehicle['marksOnGun'])
            self.assertGreaterEqual(vehicle['movingAvgDamage'], one_mark)
            # Whole percent on the wire, at or past the mark it just earned
            # and short of the next one.
            self.assertGreaterEqual(vehicle['damageRating'], 65)
            self.assertLess(vehicle['damageRating'], 85)
            self.assertEqual(2, vehicle['battleNum'])
            self.assertIn((MARKS_DB_ID, 1), vehicle['dossierPopUps'])
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(vehicle, self._result(restarted, crossing))
            receipt = crossing
            # The hangar battle-results message names the badge from the same
            # outcome.
            message = store.service_message_data(receipt['arena_unique_id'])
            self.assertEqual({TYPE_59: {'markOfMastery': 4}},
                             message['playerVehicles'])

            # A second battle at the same mark count is not a new award, and
            # the mastery badge now reports the previous best.
            again = _receipt(store.account_key, index=3, xp=ace_xp,
                             damage=one_mark)
            self.assertTrue(store.accept(again))
            vehicle = self._result(store, again)
            self.assertEqual(4, vehicle['markOfMastery'])
            self.assertEqual(4, vehicle['prevMarkOfMastery'])
            self.assertEqual(1, vehicle['marksOnGun'])
            self.assertEqual(3, vehicle['battleNum'])
            self.assertNotIn((MARKS_DB_ID, 1), vehicle['dossierPopUps'])
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(self._result(store, crossing),
                             self._result(restarted, crossing))
            self.assertTrue(restarted.acknowledge(crossing['arena_unique_id']))
            saved = json.loads(Path(path).read_text())
            self.assertNotIn(str(crossing['arena_unique_id']),
                             saved['pendingAwards'])

    def test_premium_vehicle_xp_rides_outside_the_badge_number(self):
        """Retail ranks the bare battle XP; the bonus is added on top."""
        third_class = mastery_catalog.MASTERY_XP[TYPE_59][0]
        original_factor = postbattle_store._premium_vehicle_xp_factor_100
        postbattle_store._premium_vehicle_xp_factor_100 = lambda unused: 50
        self.addCleanup(setattr, postbattle_store,
                        '_premium_vehicle_xp_factor_100', original_factor)
        _Replay.steps = []
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            # One XP short of the third class on the bare number, but well
            # past it once a 50 percent bonus is added.
            receipt = _receipt(store.account_key, xp=third_class - 1)
            self.assertTrue(store.accept(receipt))
            vehicle = self._result(store, receipt)

            self.assertEqual(0, vehicle['markOfMastery'])
            self.assertEqual(third_class - 1, vehicle['originalXP'])
            bonus = int(round((third_class - 1) * 0.5))
            self.assertEqual(third_class - 1 + bonus, vehicle['xp'])
            self.assertEqual(bonus, vehicle['premiumVehicleXP'])
            # #1513's premium-vehicle row reads a record named by the factor,
            # which only a factor step writes, so the packed field is the
            # total multiplier the chain applies.
            self.assertEqual(150, vehicle['premiumVehicleXPFactor100'])
            self.assertIn(('xp', 'SET', 'originalXP'), _Replay.steps)
            self.assertIn(('xp', 'MUL', 'premiumVehicleXPFactor100'),
                          _Replay.steps)
            self.assertIn(('freeXP', 'MUL', 'premiumVehicleXPFactor100'),
                          _Replay.steps)
            # Nothing multiplied this save, so there is no boosters row.
            self.assertEqual(0, vehicle['boosterXP'])
            self.assertNotIn(('xp', 'ADD', 'boosterXP'), _Replay.steps)
            message = store.service_message_data(receipt['arena_unique_id'])
            self.assertEqual(vehicle['xp'], message['xp'])
            # The account banks the bonus even though the badge ignored it.
            progress = store.progress()
            row = progress['vehicles']['china:Ch01_Type59']
            self.assertEqual(third_class - 1 + bonus, row['xp'])
            self.assertEqual(30 + int(round(30 * 0.5)), progress['freeXP'])

    def test_badge_state_persists_and_survives_a_restart(self):
        third_class = mastery_catalog.MASTERY_XP[TYPE_59][0]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = _receipt(store.account_key, xp=third_class,
                               damage=1200, assist_track=300,
                               assist_radio=100)
            self.assertTrue(store.accept(receipt))
            row = store.progress()['vehicles']['china:Ch01_Type59']
            self.assertEqual(1, row['markOfMastery'])
            # 1200 damage plus the largest assist of 300, smoothed from zero.
            self.assertEqual(
                battle_mastery.next_moving_average(0, 1500),
                row['movingAvgDamage'])
            self.assertGreater(row['damageRating'], 0)
            self.assertLessEqual(row['damageRating'], 10000)
            self.assertNotIn('combinedDamage', row)

            restarted = postbattle_store.PostBattleStore(path=path)
            reloaded = restarted.progress()['vehicles']['china:Ch01_Type59']
            self.assertEqual(row['markOfMastery'], reloaded['markOfMastery'])
            self.assertEqual(row['movingAvgDamage'],
                             reloaded['movingAvgDamage'])
            # Pending results preserve the original new-record notification.
            vehicle = self._result(restarted, receipt)
            self.assertEqual(1, vehicle['markOfMastery'])
            self.assertEqual(0, vehicle['prevMarkOfMastery'])

    def test_corrupt_persisted_badge_state_is_clamped_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            self.assertTrue(store.accept(_receipt(store.account_key)))
            state = json.loads(Path(path).read_text(encoding='utf8'))
            row = state['progress']['vehicles']['china:Ch01_Type59']
            row['markOfMastery'] = 97
            row['damageRating'] = -5
            row['marksOnGun'] = 'three'
            # A file written while the average was kept as a window.
            row['combinedDamage'] = list(range(200))
            Path(path).write_text(json.dumps(state), encoding='utf8')
            reloaded = postbattle_store.PostBattleStore(path=path)
            row = reloaded.progress()['vehicles']['china:Ch01_Type59']
            self.assertEqual(battle_mastery.MAX_MARK_OF_MASTERY,
                             row['markOfMastery'])
            self.assertEqual(0, row['damageRating'])
            self.assertEqual(0, row['marksOnGun'])
            self.assertNotIn('combinedDamage', row)

    def test_unresolvable_vehicle_type_still_credits_the_battle(self):
        def explode(unused_name):
            raise KeyError('no such vehicle type')

        postbattle_store._vehicle_type_compact_descr = explode
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            self.assertTrue(store.accept(
                _receipt(store.account_key, xp=99999)))
            row = store.progress()['vehicles']['china:Ch01_Type59']
            self.assertEqual(99999, row['xp'])
            self.assertEqual(0, row['markOfMastery'])
            self.assertEqual(0, row['marksOnGun'])


class PremiumVehicleFactorTests(unittest.TestCase):
    def _with_vehicles(self, module):
        import types
        items = types.ModuleType('items')
        items.vehicles = module
        return mock.patch.dict(
            sys.modules, {'items': items, 'items.vehicles': module})

    def test_the_factor_is_read_from_the_client_vehicle_type(self):
        import types
        module = types.ModuleType('items.vehicles')
        module.getVehicleType = lambda compact_descr: types.SimpleNamespace(
            premiumVehicleXPFactor=0.6)
        original = postbattle_store._vehicle_type_compact_descr
        postbattle_store._vehicle_type_compact_descr = lambda unused: TYPE_59
        self.addCleanup(setattr, postbattle_store,
                        '_vehicle_type_compact_descr', original)
        with self._with_vehicles(module):
            self.assertEqual(60, postbattle_store.
                             _premium_vehicle_xp_factor_100('china:Ch01_Type59'))

    def test_a_researchable_vehicle_and_an_unreadable_one_earn_no_bonus(self):
        import types
        module = types.ModuleType('items.vehicles')
        module.getVehicleType = lambda compact_descr: types.SimpleNamespace(
            premiumVehicleXPFactor=0.0)
        original = postbattle_store._vehicle_type_compact_descr
        postbattle_store._vehicle_type_compact_descr = lambda unused: 1
        self.addCleanup(setattr, postbattle_store,
                        '_vehicle_type_compact_descr', original)
        with self._with_vehicles(module):
            self.assertEqual(0, postbattle_store.
                             _premium_vehicle_xp_factor_100('ussr:R04_T-34'))

        def explode(unused_compact_descr):
            raise KeyError('no such vehicle type')

        module.getVehicleType = explode
        with self._with_vehicles(module):
            self.assertEqual(0, postbattle_store.
                             _premium_vehicle_xp_factor_100('ussr:R04_T-34'))

    def test_the_bonus_rounds_the_way_the_stock_replay_chain_rounds(self):
        # ``ValueReplay.__opAddCoeff`` uses round(value * factor100 / 100).
        self.assertEqual(0, postbattle_store._premium_bonus(700, 0))
        self.assertEqual(70, postbattle_store._premium_bonus(700, 10))
        self.assertEqual(4, postbattle_store._premium_bonus(75, 5))
        self.assertEqual(0, postbattle_store._premium_bonus(-5, 50))


class MasteryDossierTests(unittest.TestCase):
    def test_vehicle_dossier_carries_the_four_badge_records(self):
        written = {}

        class Dossier(object):
            def __init__(self):
                self.blocks = {'a15x15': {}, 'a15x15_2': {},
                               'max15x15': {}, 'total': {},
                               'achievements': written}

            def __getitem__(self, name):
                return self.blocks[name]

            def makeCompDescr(self):
                return 'descr'

        progress = {'vehicles': {'china:Ch01_Type59': {
            'xp': 600, 'battles': 3, 'wins': 2, 'damage': 3000, 'kills': 4,
            'changeTime': 3, 'markOfMastery': 9, 'marksOnGun': 2,
            'damageRating': 8642, 'movingAvgDamage': 2500}}}
        version, rows = data.dossiers(
            1, 0, progress, dossier_factory=lambda unused: Dossier(),
            vehicle_type_resolver=lambda unused: TYPE_59)
        self.assertEqual(data._VEHICLE_DOSSIER_VERSION, version)
        self.assertEqual([(TYPE_59, 3, 'descr')], rows)
        self.assertEqual({'markOfMastery': 4, 'marksOnGun': 2,
                          'damageRating': 8642,
                          'movingAvgDamage': 2500}, written)


if __name__ == '__main__':
    unittest.main()
