"""Award and packing contracts for #1513 post-battle achievements."""

import json
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
sys.path.insert(0, str(ROOT / 'server'))

from gui.mods.offline_lan_0922 import battle_achievements
from gui.mods.offline_lan_0922.battle_achievements import (
    ACHIEVEMENT_CONDITIONS, AWARDABLE_ACHIEVEMENTS, RECEIPT_STAT_NAMES,
    award_battle_achievements)
from gui.mods.offline_lan_0922.account_rpc import data, postbattle_store
from gui.mods.offline_lan_0922 import lan_client as lan_client_module
import lan_battle_server as lan_server_module
from lan_battle_server import BattleState, CLIENT_BUILD_0922, Player


# Exact ``dossiers2.custom.records.RECORD_DB_IDS[('achievements', name)]``
# values read from ``res/packages/scripts.pkg`` of HD 0.9.22.0.1 #1513.
RECORD_DB_IDS_0922 = {
    ('achievements', 'warrior'): 34,
    ('achievements', 'invader'): 35,
    ('achievements', 'sniper'): 36,
    ('achievements', 'defender'): 37,
    ('achievements', 'steelwall'): 38,
    ('achievements', 'supporter'): 39,
    ('achievements', 'scout'): 40,
    ('achievements', 'medalWittmann'): 49,
    ('achievements', 'medalOrlik'): 50,
    ('achievements', 'medalOskin'): 51,
    ('achievements', 'medalHalonen'): 52,
    ('achievements', 'medalBurda'): 53,
    ('achievements', 'medalBillotte'): 54,
    ('achievements', 'medalKolobanov'): 55,
    ('achievements', 'medalFadin'): 56,
    ('achievements', 'raider'): 61,
    ('achievements', 'kamikaze'): 64,
    ('achievements', 'lumberjack'): 65,
    ('achievements', 'evileye'): 72,
    ('achievements', 'medalRadleyWalters'): 73,
    ('achievements', 'medalLafayettePool'): 74,
    ('achievements', 'medalBrunoPietro'): 75,
    ('achievements', 'medalTarczay'): 76,
    ('achievements', 'medalPascucci'): 77,
    ('achievements', 'medalDumitru'): 78,
    ('achievements', 'markOfMastery'): 79,
    ('achievements', 'medalLehvaslaiho'): 106,
    ('achievements', 'medalNikolas'): 107,
    ('achievements', 'heroesOfRassenay'): 110,
    ('achievements', 'medalDeLanglade'): 145,
    ('achievements', 'medalTamadaYoshio'): 146,
    ('achievements', 'bombardier'): 147,
    ('achievements', 'huntsman'): 148,
    ('achievements', 'alaric'): 149,
    ('achievements', 'sturdy'): 150,
    ('achievements', 'ironMan'): 151,
    ('achievements', 'luckyDevil'): 152,
    ('achievements', 'sniper2'): 227,
    ('achievements', 'mainGun'): 228,
    ('achievements', 'medalMonolith'): 296,
    ('achievements', 'medalAntiSpgFire'): 297,
    ('achievements', 'medalGore'): 298,
    ('achievements', 'medalCoolBlood'): 299,
    ('achievements', 'medalStark'): 300,
}

EMPTY_STATS = dict((name, 0) for name in RECEIPT_STAT_NAMES)


def _actor(actor_kind='player', actor_id=1, team=1, **overrides):
    actor = {
        'actor_kind': actor_kind, 'actor_id': actor_id, 'team': team,
        'vehicle': 'ussr:R11_MS-1', 'tier': 5, 'vehicle_class': 'mediumTank',
        'max_health': 1000, 'health': 1000, 'survived': True, 'won': True,
        'xp': 500, 'stats': dict(EMPTY_STATS), 'kills': [],
        'damaged_targets': [], 'first_spotted': 0,
        'exclusive_spot_assists': 0, 'ever_spotted': True,
        'crits_received': 0, 'hits_received': 0,
        'damaging_hits_received': 0, 'deflected_hits_received': 0,
        'deflected_hits_at_low_health': 0,
        'potential_damage_received': 0, 'sniper_damage': 0,
        'hits_with_damage': 0, 'best_multi_kill_shot': 0,
        'lone_stand_enemies': 0, 'captured_base': False,
    }
    stats = overrides.pop('stats', None)
    if stats:
        actor['stats'].update(stats)
    actor.update(overrides)
    return actor


def _kill(victim_id, victim_tier=5, victim_class='mediumTank',
          victim_kind='bot', death_reason=0, distance=500.0,
          defended_base=False):
    return {
        'victim_kind': victim_kind, 'victim_id': victim_id,
        'victim_tier': victim_tier, 'victim_class': victim_class,
        'death_reason': death_reason, 'distance': distance,
        'defended_base': defended_base,
    }


def _awards(actors, base_captured_team=0):
    return award_battle_achievements({
        'actors': actors, 'base_captured_team': base_captured_team})


class AchievementTableTests(unittest.TestCase):
    def test_every_awardable_name_maps_to_an_exact_1513_record(self):
        for name in AWARDABLE_ACHIEVEMENTS:
            self.assertIn(
                ('achievements', name), RECORD_DB_IDS_0922,
                '%s is not a #1513 achievements record' % name)

    def test_unawarded_names_are_documented_and_never_awarded(self):
        for name, reason in battle_achievements.UNAWARDED_ACHIEVEMENTS.items():
            self.assertNotIn(name, AWARDABLE_ACHIEVEMENTS)
            self.assertTrue(reason)

    def test_thresholds_match_the_pinned_client_table(self):
        # A regression fence on the values copied out of #1513's
        # arena_achievements.ACHIEVEMENT_CONDITIONS for bonus type 1.
        self.assertEqual(ACHIEVEMENT_CONDITIONS['warrior'], {'minFrags': 6})
        self.assertEqual(
            ACHIEVEMENT_CONDITIONS['invader'], {'minCapturePts': 80})
        self.assertEqual(ACHIEVEMENT_CONDITIONS['defender'], {'minPoints': 70})
        self.assertEqual(
            ACHIEVEMENT_CONDITIONS['steelwall'],
            {'minDamage': 1000, 'minHits': 11})
        self.assertEqual(
            ACHIEVEMENT_CONDITIONS['mainGun'],
            {'minDamage': 1000, 'minDamageToTotalHealthRatio': 0.2})
        self.assertEqual(
            ACHIEVEMENT_CONDITIONS['sniper2'], {
                'minAccuracy': 0.85, 'minDamage': 1000,
                'minHitsWithDamagePercent': 0.8, 'minShots': 8,
                'sniperDistance': 300.0})
        self.assertEqual(
            ACHIEVEMENT_CONDITIONS['medalBillotte']['cmn_cnds'],
            {'hpPercentage': 20, 'minCrits': 5})


class BattleHeroTests(unittest.TestCase):
    def test_top_gun_needs_six_kills_and_goes_to_one_actor(self):
        weak = _actor(actor_id=1, stats={'kills': 5}, xp=900)
        strong = _actor(actor_id=2, stats={'kills': 6}, xp=100)
        awards = _awards([weak, strong])
        self.assertNotIn('warrior', awards[('player', 1)])
        self.assertIn('warrior', awards[('player', 2)])

    def test_top_gun_tie_breaks_on_experience(self):
        low = _actor(actor_id=1, stats={'kills': 7}, xp=100)
        high = _actor(actor_id=2, stats={'kills': 7}, xp=900)
        awards = _awards([low, high])
        self.assertNotIn('warrior', awards[('player', 1)])
        self.assertIn('warrior', awards[('player', 2)])

    def test_invader_and_defender_use_capture_statistics(self):
        invader = _actor(actor_id=1, stats={'capture_points': 80})
        defender = _actor(actor_id=2, team=2,
                          stats={'dropped_capture_points': 70})
        short = _actor(actor_id=3, team=2,
                       stats={'dropped_capture_points': 69})
        awards = _awards([invader, defender, short])
        self.assertIn('invader', awards[('player', 1)])
        self.assertIn('defender', awards[('player', 2)])
        self.assertEqual(awards[('player', 3)], [])

    def test_steel_wall_needs_potential_damage_hits_and_survival(self):
        survivor = _actor(actor_id=1, potential_damage_received=1200,
                          hits_received=11)
        dead = _actor(actor_id=2, potential_damage_received=5000,
                      hits_received=30, survived=False, health=0)
        awards = _awards([survivor, dead])
        self.assertIn('steelwall', awards[('player', 1)])
        self.assertNotIn('steelwall', awards[('player', 2)])

    def test_high_calibre_needs_a_fifth_of_the_enemy_team_health(self):
        hero = _actor(actor_id=1, team=1, stats={'damage': 1000})
        enemy = _actor(actor_kind='bot', actor_id=1, team=2, max_health=4000)
        self.assertIn('mainGun', _awards([hero, enemy])[('player', 1)])
        tougher = _actor(actor_kind='bot', actor_id=1, team=2,
                         max_health=6000)
        self.assertNotIn('mainGun', _awards([hero, tougher])[('player', 1)])

    def test_confederate_counts_enemies_finished_by_someone_else(self):
        targets = [('bot', index) for index in range(1, 7)]
        helper = _actor(actor_id=1, damaged_targets=targets)
        finisher = _actor(actor_id=2, kills=[
            _kill(index) for index in range(1, 7)])
        awards = _awards([helper, finisher])
        self.assertIn('supporter', awards[('player', 1)])
        # The actor that scored the kills did not "assist" itself.
        self.assertNotIn('supporter', awards[('player', 2)])

    def test_scout_and_patrol_duty_use_distinct_spotting_inputs(self):
        scout = _actor(actor_id=1, first_spotted=9)
        patrol = _actor(actor_id=2, exclusive_spot_assists=6)
        awards = _awards([scout, patrol])
        self.assertIn('scout', awards[('player', 1)])
        self.assertNotIn('evileye', awards[('player', 1)])
        self.assertIn('evileye', awards[('player', 2)])

    def test_tank_sniper_needs_accuracy_damaging_hits_and_range(self):
        hero = _actor(actor_id=1, sniper_damage=1200, hits_with_damage=9,
                      stats={'shots': 10, 'direct_hits': 9, 'damage': 1500})
        self.assertIn('sniper2', _awards([hero])[('player', 1)])
        inaccurate = _actor(actor_id=1, sniper_damage=1200,
                            hits_with_damage=8,
                            stats={'shots': 12, 'direct_hits': 8,
                                   'damage': 1500})
        self.assertNotIn('sniper2', _awards([inaccurate])[('player', 1)])
        close_range = _actor(actor_id=1, sniper_damage=0, hits_with_damage=9,
                             stats={'shots': 10, 'direct_hits': 9,
                                    'damage': 1500})
        self.assertNotIn('sniper2', _awards([close_range])[('player', 1)])


class EpicMedalTests(unittest.TestCase):
    def test_kill_count_medals_use_their_exact_bands(self):
        walters = _actor(actor_id=1, tier=6, stats={'kills': 8})
        pool = _actor(actor_id=2, tier=6, stats={'kills': 10})
        rassenay = _actor(actor_id=3, tier=6, stats={'kills': 14})
        awards = _awards([walters, pool, rassenay])
        self.assertIn('medalRadleyWalters', awards[('player', 1)])
        self.assertNotIn('medalLafayettePool', awards[('player', 1)])
        self.assertIn('medalLafayettePool', awards[('player', 2)])
        self.assertNotIn('medalRadleyWalters', awards[('player', 2)])
        self.assertIn('heroesOfRassenay', awards[('player', 3)])

    def test_low_tier_cannot_earn_the_tier_five_kill_medals(self):
        low = _actor(actor_id=1, tier=4, stats={'kills': 8})
        self.assertNotIn(
            'medalRadleyWalters', _awards([low])[('player', 1)])

    def test_tier_delta_medals_require_their_vehicle_class(self):
        kills = [_kill(1, victim_tier=6), _kill(2, victim_tier=7)]
        light = _actor(actor_id=1, tier=5, vehicle_class='lightTank',
                       stats={'kills': 2}, kills=kills)
        medium = _actor(actor_id=2, tier=5, vehicle_class='mediumTank',
                        stats={'kills': 2}, kills=kills)
        heavy = _actor(actor_id=3, tier=5, vehicle_class='heavyTank',
                       stats={'kills': 2}, kills=kills)
        awards = _awards([light, medium, heavy])
        self.assertIn('medalOrlik', awards[('player', 1)])
        self.assertNotIn('medalOrlik', awards[('player', 2)])
        self.assertIn('medalLehvaslaiho', awards[('player', 2)])
        self.assertEqual(awards[('player', 3)], [])

    def test_same_tier_kills_do_not_satisfy_a_tier_delta_medal(self):
        light = _actor(
            actor_id=1, tier=5, vehicle_class='lightTank',
            stats={'kills': 2},
            kills=[_kill(1, victim_tier=5), _kill(2, victim_tier=5)])
        self.assertNotIn('medalOrlik', _awards([light])[('player', 1)])

    def test_artillery_hunting_medals_exclude_artillery_drivers(self):
        kills = [_kill(index, victim_class='SPG', victim_tier=6)
                 for index in range(1, 4)]
        tank = _actor(actor_id=1, tier=5, vehicle_class='heavyTank',
                      stats={'kills': 3}, kills=kills)
        artillery = _actor(actor_id=2, tier=5, vehicle_class='SPG',
                           stats={'kills': 3}, kills=kills)
        awards = _awards([tank, artillery])
        self.assertIn('medalDumitru', awards[('player', 1)])
        self.assertIn('medalBurda', awards[('player', 1)])
        self.assertNotIn('medalDumitru', awards[('player', 2)])
        self.assertNotIn('medalBurda', awards[('player', 2)])

    def test_billotte_family_needs_crits_low_health_survival_and_a_win(self):
        base = dict(tier=5, vehicle_class='heavyTank', crits_received=5,
                    health=150, max_health=1000)
        billotte = _actor(actor_id=1, stats={'kills': 2}, **base)
        self.assertIn('medalBillotte', _awards([billotte])[('player', 1)])
        healthy = _actor(actor_id=1, stats={'kills': 2},
                         **dict(base, health=900))
        self.assertNotIn('medalBillotte', _awards([healthy])[('player', 1)])
        few_crits = _actor(actor_id=1, stats={'kills': 2},
                           **dict(base, crits_received=4))
        self.assertNotIn('medalBillotte', _awards([few_crits])[('player', 1)])
        defeated = _actor(actor_id=1, stats={'kills': 2},
                          **dict(base, won=False))
        self.assertNotIn('medalBillotte', _awards([defeated])[('player', 1)])
        tarczay = _actor(actor_id=1, stats={'kills': 5}, **base)
        self.assertIn('medalTarczay', _awards([tarczay])[('player', 1)])
        self.assertNotIn('medalBillotte', _awards([tarczay])[('player', 1)])

    def test_kolobanov_needs_a_lone_stand_and_a_win(self):
        winner = _actor(actor_id=1, lone_stand_enemies=5)
        self.assertIn('medalKolobanov', _awards([winner])[('player', 1)])
        loser = _actor(actor_id=1, lone_stand_enemies=5, won=False)
        self.assertNotIn('medalKolobanov', _awards([loser])[('player', 1)])
        outnumbered_less = _actor(actor_id=1, lone_stand_enemies=4)
        self.assertNotIn(
            'medalKolobanov', _awards([outnumbered_less])[('player', 1)])

    def test_de_langlade_counts_only_base_attackers(self):
        inside = _actor(actor_id=1, stats={'kills': 4}, kills=[
            _kill(index, defended_base=True) for index in range(1, 5)])
        self.assertIn('medalDeLanglade', _awards([inside])[('player', 1)])
        outside = _actor(actor_id=1, stats={'kills': 4}, kills=[
            _kill(index) for index in range(1, 5)])
        self.assertNotIn('medalDeLanglade', _awards([outside])[('player', 1)])

    def test_artillery_medals_require_a_self_propelled_gun(self):
        gore_stats = {'damage': 8000, 'kills': 2}
        artillery = _actor(actor_id=1, vehicle_class='SPG',
                           max_health=800, stats=gore_stats,
                           damaging_hits_received=2,
                           kills=[_kill(1, victim_class='SPG'),
                                  _kill(2, victim_class='SPG',
                                        distance=50.0)])
        awards = _awards([artillery])[('player', 1)]
        self.assertIn('medalGore', awards)
        self.assertIn('medalStark', awards)
        self.assertIn('medalAntiSpgFire', awards)
        tank = _actor(actor_id=1, vehicle_class='heavyTank',
                      max_health=800, stats=gore_stats,
                      damaging_hits_received=2,
                      kills=[_kill(1, victim_class='SPG'),
                             _kill(2, victim_class='SPG', distance=50.0)])
        for name in ('medalGore', 'medalStark', 'medalAntiSpgFire',
                     'medalCoolBlood'):
            self.assertNotIn(name, _awards([tank])[('player', 1)])

    def test_cool_blood_counts_close_kills_only(self):
        close = _actor(actor_id=1, vehicle_class='SPG', stats={'kills': 2},
                       kills=[_kill(1, distance=40.0),
                              _kill(2, distance=99.0)])
        self.assertIn('medalCoolBlood', _awards([close])[('player', 1)])
        far = _actor(actor_id=1, vehicle_class='SPG', stats={'kills': 2},
                     kills=[_kill(1, distance=140.0),
                            _kill(2, distance=99.0)])
        self.assertNotIn('medalCoolBlood', _awards([far])[('player', 1)])

    def test_bombardier_kamikaze_sturdy_and_raider(self):
        bomber = _actor(actor_id=1, best_multi_kill_shot=2)
        self.assertIn('bombardier', _awards([bomber])[('player', 1)])
        rammer = _actor(actor_id=1, tier=5, stats={'kills': 1}, kills=[
            _kill(1, victim_tier=6, death_reason=2)])
        self.assertIn('kamikaze', _awards([rammer])[('player', 1)])
        shot_kill = _actor(actor_id=1, tier=5, stats={'kills': 1}, kills=[
            _kill(1, victim_tier=6, death_reason=0)])
        self.assertNotIn('kamikaze', _awards([shot_kill])[('player', 1)])
        spartan = _actor(actor_id=1, health=90, max_health=1000,
                         deflected_hits_at_low_health=1)
        self.assertIn('sturdy', _awards([spartan])[('player', 1)])
        # A shot that bounced while the vehicle was still healthy is not it.
        healthy_bounce = _actor(actor_id=1, health=90, max_health=1000,
                                deflected_hits_received=3)
        self.assertNotIn('sturdy', _awards([healthy_bounce])[('player', 1)])
        dead = _actor(actor_id=1, health=0, max_health=1000, survived=False,
                      deflected_hits_at_low_health=1)
        self.assertNotIn('sturdy', _awards([dead])[('player', 1)])
        raider = _actor(actor_id=1, team=1, captured_base=True,
                        ever_spotted=False)
        self.assertIn(
            'raider', _awards([raider], base_captured_team=1)[('player', 1)])
        seen = _actor(actor_id=1, team=1, captured_base=True,
                      ever_spotted=True)
        self.assertNotIn(
            'raider', _awards([seen], base_captured_team=1)[('player', 1)])

    def test_bots_earn_the_same_medals_as_human_players(self):
        bot = _actor(actor_kind='bot', actor_id=3, stats={'kills': 6})
        self.assertIn('warrior', _awards([bot])[('bot', 3)])

    def test_an_empty_battle_awards_nothing(self):
        self.assertEqual(_awards([]), {})


class ResultPackingTests(unittest.TestCase):
    """The exact #1513 fields the results window reads."""

    def setUp(self):
        self._native = (postbattle_store._vehicle_type_compact_descr,
                        postbattle_store._arena_type_id)
        postbattle_store._vehicle_type_compact_descr = lambda unused: 50001
        postbattle_store._arena_type_id = lambda unused: 70001

    def tearDown(self):
        (postbattle_store._vehicle_type_compact_descr,
         postbattle_store._arena_type_id) = self._native

    @staticmethod
    def _receipt(achievements, row_achievements=None):
        stats = dict(EMPTY_STATS)
        stats.update({'kills': 6, 'damage': 2000, 'hits_received': 12,
                      'potential_damage_received': 3000})
        row = {
            'actor_kind': 'player', 'actor_id': 1, 'name': 'Alice',
            'vehicle': 'ussr:R11_MS-1', 'team': 1, 'health': 100,
            'death_reason': -1, 'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': 600, 'stats': dict(stats),
            'achievements': list(achievements),
        }
        rows = [row]
        if row_achievements is not None:
            rows.append({
                'actor_kind': 'bot', 'actor_id': 2, 'name': 'Bot',
                'vehicle': 'ussr:R11_MS-1', 'team': 2, 'health': 0,
                'death_reason': 0, 'killer_kind': 'player', 'killer_id': 1,
                'is_team_killer': False, 'xp': 10,
                'stats': dict(EMPTY_STATS),
                'achievements': list(row_achievements),
            })
        return {
            'receipt_id': 'server:7:1', 'arena_unique_id': (7 << 32) | 1,
            'round_id': 7, 'player_id': 1,
            'account_key': 'account-key-123456', 'player_name': 'Alice',
            'vehicle': 'ussr:R11_MS-1', 'team': 1, 'winner': 1,
            'map': '01_karelia', 'finish_reason': 1, 'death_reason': -1,
            'duration': 120, 'premature_leave': False,
            'stats': dict(stats),
            'rewards': {'credits': 4200, 'xp': 600, 'free_xp': 30,
                        'repair_cost': 0, 'ammo_cost': 0},
            'public_results': rows,
        }

    def test_names_map_to_exact_record_database_ids(self):
        records = postbattle_store._achievement_records(
            ['warrior', 'medalKolobanov', 'sniper2'],
            record_db_ids=RECORD_DB_IDS_0922)
        self.assertEqual(records, [
            ('warrior', 34), ('medalKolobanov', 55), ('sniper2', 227)])

    def test_unregistered_names_are_dropped_before_they_reach_the_client(self):
        self.assertEqual(postbattle_store._achievement_records(
            ['warrior', 'notAMedal'],
            record_db_ids=RECORD_DB_IDS_0922), [('warrior', 34)])

    def test_pop_ups_carry_the_running_account_total(self):
        packers = _Packers()
        postbattle_store.pack_battle_result(
            self._receipt(['warrior', 'medalKolobanov'],
                          row_achievements=['scout']),
            packers=packers, replay_types=(_Replay, _ReplayConnector),
            record_db_ids=RECORD_DB_IDS_0922,
            achievement_counts={'warrior': 12})
        personal, = [value for name, value in packers.calls
                     if name == 'VEH_FULL_RESULTS']
        self.assertEqual(personal['achievements'], [34, 55])
        self.assertEqual(personal['dossierPopUps'], [(34, 12), (55, 1)])
        self.assertEqual(personal['directHitsReceived'], 12)
        self.assertEqual(personal['potentialDamageReceived'], 3000)
        public = [value for name, value in packers.calls
                  if name == 'VEH_PUBLIC_RESULTS']
        self.assertEqual(
            sorted(row['achievements'] for row in public), [[34, 55], [40]])

    def test_two_vehicles_share_one_compact_descriptor_slot(self):
        # Both roster rows use the same vehicle here, so the public map must
        # still address them by their distinct projected vehicle IDs.
        packers = _Packers()
        postbattle_store.pack_battle_result(
            self._receipt(['warrior'], row_achievements=[]),
            packers=packers, replay_types=(_Replay, _ReplayConnector),
            record_db_ids=RECORD_DB_IDS_0922)
        public = [value for name, value in packers.calls
                  if name == 'VEH_PUBLIC_RESULTS']
        self.assertEqual([row['achievements'] for row in public], [[34], []])

    def test_a_battle_without_medals_packs_empty_lists(self):
        packers = _Packers()
        postbattle_store.pack_battle_result(
            self._receipt([]), packers=packers,
            replay_types=(_Replay, _ReplayConnector),
            record_db_ids=RECORD_DB_IDS_0922)
        personal, = [value for name, value in packers.calls
                     if name == 'VEH_FULL_RESULTS']
        self.assertEqual(personal['achievements'], [])
        self.assertEqual(personal['dossierPopUps'], [])


class DossierAccumulationTests(unittest.TestCase):
    def test_progress_counts_each_medal_for_account_and_vehicle(self):
        store = postbattle_store.PostBattleStore(path=None)
        receipt = ResultPackingTests._receipt(['warrior', 'medalKolobanov'])
        receipt['account_key'] = store.account_key
        self.assertTrue(store.accept(receipt))
        second = dict(receipt)
        second['receipt_id'] = 'server:8:1'
        second['arena_unique_id'] = (8 << 32) | 1
        second['round_id'] = 8
        second['public_results'] = [
            dict(row, achievements=['warrior'])
            for row in receipt['public_results']]
        second['achievements'] = ['warrior']
        self.assertTrue(store.accept(second))
        progress = store.progress()
        self.assertEqual(
            progress['achievements'], {'warrior': 2, 'medalKolobanov': 1})
        self.assertEqual(
            progress['vehicles']['ussr:R11_MS-1']['achievements'],
            {'warrior': 2, 'medalKolobanov': 1})

    def test_vehicle_dossier_writes_counters_and_the_hero_aggregate(self):
        rows = data.dossiers(
            postbattle_progress={'vehicles': {'ussr:R11_MS-1': {
                'battles': 3, 'wins': 2, 'changeTime': 3,
                'achievements': {'warrior': 2, 'medalKolobanov': 1},
            }}},
            dossier_factory=_FakeDossier,
            vehicle_type_resolver=lambda name: 1)[1]
        self.assertEqual(len(rows), 1)
        blocks = rows[0][2]
        self.assertEqual(blocks['achievements']['warrior'], 2)
        self.assertEqual(blocks['achievements']['medalKolobanov'], 1)
        # ``warrior`` is a battle hero; Kolobanov's medal is not.
        self.assertEqual(blocks['achievements']['battleHeroes'], 2)

    def test_account_dossier_carries_the_running_medal_counters(self):
        compact = data.account_dossier(
            {'achievements': {'warrior': 5, 'medalKolobanov': 1}},
            dossier_factory=_FakeDossier)
        self.assertEqual(compact['achievements'], {
            'warrior': 5, 'medalKolobanov': 1, 'battleHeroes': 5})

    def test_account_dossier_stays_empty_without_medals(self):
        self.assertEqual(data.account_dossier({}, _FakeDossier), '')
        self.assertEqual(
            data.account_dossier({'achievements': {}}, _FakeDossier), '')

    def test_account_snapshot_publishes_the_dossier_key(self):
        snapshot = data.stats(postbattle_progress={'achievements': {}})
        self.assertEqual(snapshot['stats']['dossier'], '')

    def test_a_vehicle_without_medals_leaves_the_block_untouched(self):
        rows = data.dossiers(
            postbattle_progress={'vehicles': {'ussr:R11_MS-1': {
                'battles': 1, 'changeTime': 1, 'achievements': {},
            }}},
            dossier_factory=_FakeDossier,
            vehicle_type_resolver=lambda name: 1)[1]
        self.assertNotIn('achievements', rows[0][2])


class ServerReceiptTests(unittest.TestCase):
    """The LAN server decides awards and freezes them into the receipt."""

    @staticmethod
    def _battle():
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        player = Player(1, _NullSocket(), ('127.0.0.1', 1), name='Alice',
                        vehicle='ussr:R11_MS-1', team=1,
                        account_key='a' * 32)
        player.max_health = 1000
        player.health = 1000
        state.players = {1: player}
        state.vehicle_catalogs = {1: (
            {'name': 'ussr:R11_MS-1', 'level': 5,
             'tags': ('lightTank',)},
            {'name': 'germany:G04_PzVI_Tiger_I', 'level': 7,
             'tags': ('heavyTank',)},
        )}
        state._freeze_round_participants((player,))
        state.bot_manifest = [
            {'id': index, 'team': 2, 'slot': index, 'name': 'Bot-%d' % index,
             'vehicle': 'germany:G04_PzVI_Tiger_I', 'health': 0,
             'max_health': 1500}
            for index in range(1, 7)]
        state.bot_states = dict(
            (entry['id'], dict(entry, alive=False, death_reason=0,
                               death_attacker_kind='player',
                               death_attacker_id=1))
            for entry in state.bot_manifest)
        return state, player

    def test_receipt_carries_awards_and_the_new_result_statistics(self):
        state, unused_player = self._battle()
        for bot_id in range(1, 7):
            state._record_frag(
                'player', 1, 2, 'bot', bot_id, attacker_team=1,
                projectile_id=bot_id, distance=420.0)
        state._statistics_row('player', 1).update({
            'shots_fired': 10, 'shots_hit': 8, 'damage_dealt': 6000,
            'hits_received': 12, 'potential_damage_received': 2400,
            'crits_received_mask': 0b1011,
        })

        self.assertTrue(state._finish_battle(1, 'team_eliminated'))

        receipt = list(state.result_receipts.values())[-1]
        rows = dict(((row['actor_kind'], row['actor_id']), row)
                    for row in receipt['public_results'])
        personal = rows['player', 1]
        # Six kills of tier-7 heavies in a tier-5 light tank.
        self.assertIn('warrior', personal['achievements'])
        self.assertIn('medalOrlik', personal['achievements'])
        self.assertIn('mainGun', personal['achievements'])
        self.assertEqual(12, personal['stats']['hits_received'])
        self.assertEqual(
            2400, personal['stats']['potential_damage_received'])
        self.assertEqual(3, personal['stats']['crits_received'])
        for bot_id in range(1, 7):
            self.assertEqual([], rows['bot', bot_id]['achievements'])

    def test_awards_survive_the_persisted_receipt_validator(self):
        state, unused_player = self._battle()
        for bot_id in range(1, 7):
            state._record_frag(
                'player', 1, 2, 'bot', bot_id, attacker_team=1)
        self.assertTrue(state._finish_battle(1, 'team_eliminated'))
        receipt = list(state.result_receipts.values())[-1]
        reloaded = lan_server_module._persisted_result_receipt(
            json.loads(json.dumps(receipt)))
        self.assertEqual(
            reloaded['public_results'][0]['achievements'],
            receipt['public_results'][0]['achievements'])

    def test_the_validator_rejects_an_unknown_medal_name(self):
        state, unused_player = self._battle()
        self.assertTrue(state._finish_battle(1, 'team_eliminated'))
        receipt = json.loads(json.dumps(
            list(state.result_receipts.values())[-1]))
        receipt['public_results'][0]['achievements'] = ['notAMedal']
        with self.assertRaises(ValueError):
            lan_server_module._persisted_result_receipt(receipt)

    def test_a_pre_achievement_receipt_still_loads(self):
        state, unused_player = self._battle()
        self.assertTrue(state._finish_battle(1, 'team_eliminated'))
        receipt = json.loads(json.dumps(
            list(state.result_receipts.values())[-1]))
        for row in receipt['public_results']:
            row.pop('achievements')
            for name in ('hits_received', 'potential_damage_received',
                         'crits_received'):
                row['stats'].pop(name)
        for name in ('hits_received', 'potential_damage_received',
                     'crits_received'):
            receipt['stats'].pop(name)
        reloaded = lan_server_module._persisted_result_receipt(receipt)
        self.assertEqual(reloaded['public_results'][0]['achievements'], [])
        self.assertEqual(reloaded['stats']['hits_received'], 0)
        self.assertTrue(
            lan_client_module._valid_battle_receipt(reloaded))


class CaptureAndLoneStandTests(unittest.TestCase):
    """Round state #1513 medals read that the server did not record before."""

    def test_dropped_capture_points_credit_the_enemy_that_caused_them(self):
        state, unused_player = ServerReceiptTests._battle()
        state.capture_contributors[1]['bot:3'] = 40
        dropped = state._drop_capture_for_vehicle(
            'bot', 3, attacker=('player', 1))
        self.assertEqual(dropped, 40)
        self.assertEqual(
            state._statistics_row('player', 1)['dropped_capture_points'], 40)

    def test_a_teammate_never_earns_dropped_capture_points(self):
        state, unused_player = ServerReceiptTests._battle()
        state.bot_states[1]['team'] = 1
        state.capture_contributors[2]['bot:1'] = 20
        state._drop_capture_for_vehicle('bot', 1, attacker=('player', 1))
        self.assertEqual(
            state._statistics_row('player', 1)['dropped_capture_points'], 0)

    def test_capture_points_accumulate_for_the_vehicle_in_the_circle(self):
        state, unused_player = self._capture_battle()
        self.assertTrue(state._update_capture())
        self.assertEqual(
            state._statistics_row('player', 1)['capture_points'], 1)
        self.assertIn(('player', 1), state.capture_invaders[2])
        state.tick += int(round(lan_server_module.TICK_HZ))
        state._update_capture()
        self.assertEqual(
            state._statistics_row('player', 1)['capture_points'], 2)

    @staticmethod
    def _capture_battle():
        state, player = ServerReceiptTests._battle()
        state.bot_states = {}
        state.bot_manifest = []
        state.roster_finalized = True
        player.client_position = True
        player.x, player.z = 100.0, 100.0
        player.alive = True
        player.participating = True
        player.connected = True
        state.capture_bases = {2: [(100.0, 100.0)]}
        # ``_update_capture`` only runs on a whole second of live combat.
        tick_hz = int(round(lan_server_module.TICK_HZ))
        state.tick = tick_hz * int(
            round(lan_server_module.PREBATTLE_SECONDS)) + tick_hz
        return state, player

    def test_a_one_vehicle_team_records_no_lone_stand(self):
        state, unused_player = ServerReceiptTests._battle()
        state.bot_states = dict(
            (entry['id'], dict(entry, alive=True))
            for entry in state.bot_manifest)
        state._record_lone_stands()
        self.assertEqual(state.lone_stand_enemies, {})

    def test_the_last_survivor_of_a_real_team_records_its_odds(self):
        state, unused_player = ServerReceiptTests._battle()
        state.bot_states = dict(
            (entry['id'], dict(entry, alive=True))
            for entry in state.bot_manifest)
        # Give the human a dead team mate so the team really had two.
        state.bot_states[7] = {'id': 7, 'team': 1, 'alive': False,
                               'vehicle': 'ussr:R11_MS-1', 'max_health': 1000}
        state._record_lone_stands()
        self.assertEqual(
            state.lone_stand_enemies, {('player', 1): 6})


class CritsMaskTests(unittest.TestCase):
    """#1513 ``crits_mask_parser`` bit positions."""

    @staticmethod
    def _state(devices=(), destroyed=(), crew=()):
        return {
            'devices': [{'name': name, 'state': state}
                        for name, state in devices],
            'destroyed': list(destroyed),
            'crew_ko': list(crew),
        }

    def test_critical_devices_occupy_the_low_byte(self):
        mask = lan_server_module._crits_mask(
            {}, self._state(devices=(('engineHealth', 'critical'),)))
        self.assertEqual(mask, 1 << 0)
        mask = lan_server_module._crits_mask(
            {}, self._state(devices=(('gunHealth', 'critical'),)))
        self.assertEqual(mask, 1 << 5)

    def test_destroyed_devices_start_at_bit_twelve_and_tracks_share_a_slot(
            self):
        left = lan_server_module._crits_mask(
            {}, self._state(destroyed=('leftTrackHealth',)))
        right = lan_server_module._crits_mask(
            {}, self._state(destroyed=('rightTrackHealth',)))
        self.assertEqual(left, 1 << (12 + 4))
        self.assertEqual(left, right)

    def test_crew_starts_at_bit_twenty_four_and_numbered_roles_share_a_slot(
            self):
        self.assertEqual(
            lan_server_module._crits_mask({}, self._state(crew=('driver',))),
            1 << (24 + 1))
        self.assertEqual(
            lan_server_module._crits_mask({}, self._state(crew=('loader2',))),
            1 << (24 + 4))

    def test_a_repeated_crit_sets_no_new_bit(self):
        previous = self._state(destroyed=('gunHealth',))
        self.assertEqual(lan_server_module._crits_mask(previous, previous), 0)


class _NullSocket(object):
    def sendall(self, unused_payload):
        pass


class _FakeDossier(object):
    """Only the block access that ``data.dossiers`` performs."""

    def __init__(self, unused_compact_descr):
        self.blocks = {}

    def __getitem__(self, name):
        return self.blocks.setdefault(name, {})

    def makeCompDescr(self):
        return self.blocks


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


class _ReplayConnector(object):
    def __init__(self, unused_packer, values):
        self.values = values


class _Replay(object):
    def __init__(self, connector, recordName=None, startRecordName=None):
        self.connector = connector
        self.record_name = recordName
        self.start_name = startRecordName

    def pack(self):
        return ('SET:%s:%s' % (
            self.record_name, self.connector.values[self.start_name]
        )).encode('ascii')


if __name__ == '__main__':
    unittest.main()
