"""Full approachable-achievement settlement, including career-only awards."""
import os
import tempfile
import unittest
from unittest import mock

import test_port_0922_battle_achievements as f


class CommendationTests(unittest.TestCase):
    def awards(self, **values):
        return f._awards([f._actor(**values)])[('player', 1)]

    def test_health_relative_awards_exclude_equality_and_spg_fire_for_effect(self):
        for damage, expected in ((1000, False), (1001, True)):
            medals = self.awards(stats={'damage': damage, 'damage_blocked': damage})
            self.assertEqual(expected, 'impenetrable' in medals)
            self.assertEqual(expected, 'shootToKill' in medals)
        self.assertNotIn('impenetrable', self.awards(
            survived=False, stats={'damage_blocked': 5000}))
        self.assertNotIn('shootToKill', self.awards(
            vehicle_class='SPG', stats={'damage': 5000}))

    def test_fighter_is_four_or_five_kills_without_one_winner_limit(self):
        for kills in (3, 4, 5, 6):
            self.assertEqual(kills in (4, 5), 'fighter' in self.awards(stats={'kills': kills}))
        medals = f._awards([f._actor(actor_id=i, stats={'kills': 4}) for i in (1, 2)])
        self.assertTrue(all('fighter' in values for values in medals.values()))

    def test_duelist_requires_two_killed_enemies_who_damaged_the_actor(self):
        kills = [f._kill(1), f._kill(2), f._kill(3)]
        self.assertNotIn('duelist', self.awards(kills=kills, duelist_sources=[('bot', 1)]))
        self.assertIn('duelist', self.awards(kills=kills, duelist_sources=[('bot', 1), ('bot', 2)]))

    def test_demolition_arsonist_bruiser_and_hand_of_god_boundaries(self):
        kills = [dict(f._kill(2, death_reason=1), ammo_rack=True)]
        medals = self.awards(kills=kills, critical_hits=5,
                             damage_sources=[('bot', i) for i in range(4)])
        for name in ('demolition', 'arsonist', 'bonecrusher', 'charmed'):
            self.assertIn(name, medals)
        self.assertNotIn('bonecrusher', self.awards(critical_hits=4))
        sources = [('bot', i) for i in range(4)]
        self.assertNotIn('charmed', self.awards(damage_sources=sources[:3]))
        self.assertNotIn('charmed', self.awards(damage_sources=sources, won=False))
        self.assertNotIn('charmed', self.awards(damage_sources=sources, survived=False))

    def test_spotter_counts_only_radio_assist_on_a_win(self):
        self.assertIn('aimer', self.awards(stats={'assist_radio': 1000}))
        self.assertNotIn('aimer', self.awards(stats={'assist_radio': 999, 'assist_track': 5000}))
        self.assertNotIn('aimer', self.awards(stats={'assist_radio': 2000}, won=False))

    def test_mutual_kills_include_ram_and_posthumous_fire(self):
        for reason in (0, 1, 2):
            actors = [f._actor(kills=[f._kill(2, death_reason=reason)], survived=False),
                      f._actor(actor_kind='bot', actor_id=2, team=2, survived=False,
                               kills=[f._kill(1, victim_kind='player', death_reason=reason)])]
            medals = f._awards(actors)
            self.assertIn('even', medals[('player', 1)])
            self.assertIn('even', medals[('bot', 2)])
        self.assertNotIn('even', self.awards(kills=[f._kill(2)], survived=False))

    def test_server_projects_module_only_duels_and_repeated_critical_transitions(self):
        state, player = f.ServerReceiptTests._battle()
        player.health = 500
        damaged = {'devices': [{'name': 'engineHealth', 'state': 'critical'}]}
        for bot_id in range(1, 5):
            state._record_damage(('bot', bot_id), ('player', 1), 10, {})
        # Damage then repair then damage again counts as two events.
        for unused in range(5):
            state._record_critical_damage(('player', 1), ('bot', 1), {}, damaged)
        # A module-only hit also qualifies a Duelist opponent.
        state._record_critical_damage(('bot', 5), ('player', 1), {}, damaged)
        state.bot_states[5]['critical'] = {'ammo_rack_death': True}
        state.bot_states[6]['death_reason'] = 1
        for bot_id in (1, 5, 6):
            state._record_frag('player', 1, 2, 'bot', bot_id)
        self.assertTrue(state._finish_battle(1, 'team_eliminated'))
        receipt = list(state.result_receipts.values())[-1]
        personal = next(row for row in receipt['public_results']
                        if (row['actor_kind'], row['actor_id']) == ('player', 1))
        expected = {'duelist', 'bonecrusher', 'demolition', 'arsonist', 'charmed'}
        self.assertTrue(expected <= set(personal['achievements']))
        self.assertTrue(f.lan_client_module._valid_battle_receipt(receipt))
        self.assertEqual(receipt['public_results'],
                         f.lan_server_module._persisted_result_receipt(receipt)['public_results'])

    def test_module_damage_to_allies_breaks_the_series_but_harmless_hits_do_not(self):
        state, player = f.ServerReceiptTests._battle()
        state.bot_states[1]['team'] = 1
        damaged = {'devices': [{'name': 'engineHealth', 'state': 'critical'}]}
        state._record_critical_damage(('player', 1), ('bot', 1), {}, {})
        self.assertEqual(0, state._receipt_statistics(state._statistics_row('player', 1))['team_crits'])
        state._record_critical_damage(('player', 1), ('bot', 1), {}, damaged)
        self.assertEqual(1, state._receipt_statistics(state._statistics_row('player', 1))['team_crits'])


class CareerCommendationTests(unittest.TestCase):
    def receipt(self, store, index, medals=(), **stats):
        receipt = f.ResultPackingTests._receipt(medals)
        receipt.update(account_key=store.account_key, receipt_id='series:%d:1' % index,
                       round_id=index, arena_unique_id=(index << 32) | 1)
        receipt['stats'].update(stats)
        receipt['public_results'][0]['stats'].update(stats)
        return receipt

    def test_fifty_clean_battles_survive_reload_and_award_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'results.json')
            store = f.postbattle_store.PostBattleStore(path=path)
            for index in range(1, 50):
                receipt = self.receipt(store, index)
                if index % 2:
                    receipt['vehicle'] = 'germany:G04_PzVI_Tiger_I'
                    receipt['public_results'][0]['vehicle'] = receipt['vehicle']
                self.assertTrue(store.accept(receipt))
                store.acknowledge(receipt['arena_unique_id'])
            store = f.postbattle_store.PostBattleStore(path=path)
            final = self.receipt(store, 50)
            self.assertTrue(store.accept(final))
            self.assertFalse(store.accept(final))
            reloaded = f.postbattle_store.PostBattleStore(path=path)
            self.assertFalse(reloaded.accept(final))
            progress = reloaded.progress()
            self.assertEqual(1, progress['achievements']['reliableComrade'])
            self.assertNotIn('reliableComrade', progress['vehicles'][final['vehicle']]['achievements'])
            pending = reloaded._pending[str(final['arena_unique_id'])]
            self.assertIn('reliableComrade', pending['achievements'])
            self.assertIn('reliableComrade', pending['public_results'][0]['achievements'])

    def test_failed_save_rolls_back_the_fiftieth_battle_award(self):
        store = f.postbattle_store.PostBattleStore(path=None)
        store._progress['achievements'] = {'reliableComradeSeries': 49}
        receipt = self.receipt(store, 50)
        with mock.patch.object(store, '_save', side_effect=IOError('disk full')):
            with self.assertRaises(IOError):
                store.accept(receipt)
        self.assertEqual({'reliableComradeSeries': 49}, store.progress()['achievements'])
        self.assertFalse(store._pending)
        self.assertTrue(store.accept(receipt))
        self.assertEqual(1, store.progress()['achievements']['reliableComrade'])

    def test_friendly_modules_reset_and_training_does_not_extend_series(self):
        store = f.postbattle_store.PostBattleStore(path=None)
        store._progress['achievements'] = {'reliableComradeSeries': 49}
        receipt = self.receipt(store, 1, team_crits=1)
        store.accept(receipt)
        self.assertNotIn('reliableComrade', store.progress()['achievements'])
        self.assertNotIn('reliableComradeSeries', store.progress()['achievements'])
        receipt = self.receipt(store, 2)
        receipt['battle_mode'] = 'training'
        store.accept(receipt)
        self.assertNotIn('reliableComradeSeries', store.progress()['achievements'])

    def test_spotter_writes_single_flag_and_best_series_in_the_right_blocks(self):
        store = f.postbattle_store.PostBattleStore(path=None)
        for index, assist in enumerate((3000, 1000, 5000), 1):
            store.accept(self.receipt(store, index, ['aimer'], assist_radio=assist))
        counters = store.progress()['achievements']
        self.assertEqual(1, counters['aimer'])
        self.assertEqual(5, counters['maxAimerSeries'])
        dossier = f.data.account_dossier({'achievements': counters}, f._FakeDossier)
        self.assertEqual(1, dossier['singleAchievements']['aimer'])
        self.assertEqual(5, dossier['achievements']['maxAimerSeries'])
        self.assertNotIn('aimer', dossier['achievements'])

    def test_all_commendations_reach_personal_ribbons_and_public_results(self):
        names = f.battle_achievements.APPROACHABLE_ACHIEVEMENTS
        receipt = f.ResultPackingTests._receipt(names)
        packers = f._Packers()
        with mock.patch.object(f.postbattle_store, '_vehicle_type_compact_descr', return_value=1), \
                mock.patch.object(f.postbattle_store, '_arena_type_id', return_value=1):
            f.postbattle_store.pack_battle_result(receipt, packers=packers,
                replay_types=(f._Replay, f._ReplayConnector),
                record_db_ids=f.RECORD_DB_IDS_0922,
                achievement_counts={'aimer': 1, 'maxAimerSeries': 5})
        full = next(value for name, value in packers.calls if name == 'VEH_FULL_RESULTS')
        public = next(value for name, value in packers.calls if name == 'VEH_PUBLIC_RESULTS')
        ids = {517, 519, 521, 522, 523, 524, 525, 526, 527, 528, 529}
        self.assertEqual(ids, set(full['achievements']))
        self.assertEqual(ids, set(public['achievements']))
        self.assertEqual(ids, {row[0] for row in full['dossierPopUps']})
        self.assertIn((529, 5), full['dossierPopUps'])


if __name__ == '__main__':
    unittest.main()
