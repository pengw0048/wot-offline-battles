"""TD9 uses consecutive verified shots, ordered by launch rather than arrival."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import test_port_0922_server_projectiles as projectile
from test_port_0922_personal_campaign_battle import node, definition
from test_port_0922_postbattle import (
    _receipt, _latest_receipt, postbattle_store,
    lan_server_module as server, lan_client_module as client)
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy


AUTHORITY = projectile.SIMULATION_WORKER_AUTHORITY_ID
DEFINITIONS = [quest for quest in json.loads((Path(__file__).parent /
    'fixtures/personal_mission_td789_conditions_0922.json').read_text())['missions']
               if quest['number'] == 9]


class PiercingSeriesProjectileTests(unittest.TestCase):
    def setUp(self):
        self.state = projectile._state(players=4)

    def launch(self, shooter=1, **changes):
        message = projectile._launch(shooter_id=shooter, **changes)
        self.assertTrue(projectile._launch_authority(self.state, message))
        return '%s:p:%s:%s' % (self.state.round_id, shooter, message['shot_seq'])

    def resolve(self, identity, result=2, **changes):
        direct = projectile._effect(damage=1 if result == 2 else 0,
                                    shot_result=result)
        message = projectile._resolve(identity, direct=direct)
        message.update(changes)
        self.assertTrue(self.state.resolve_projectile(AUTHORITY, message))
        return message

    def series(self, shooter=1):
        return self.state._statistics_row('player', shooter)['max_piercing_series']

    def test_misses_and_nonpenetrating_hits_break_only_the_current_series(self):
        outcomes = (2, 2, None, 2, 2, 2, 1, 2, 0, 2)
        expected = (1, 2, 2, 2, 2, 3, 3, 3, 3, 3)
        for result, maximum in zip(outcomes, expected):
            identity = self.launch()
            if result is None:
                self.resolve(identity, direct=None, outcome='miss', impact=None)
            else:
                self.resolve(identity, result)
            self.assertEqual(maximum, self.series())

    def test_delayed_success_merges_only_adjacent_launch_ordinals(self):
        identities = [self.launch() for unused in range(4)]
        for index, maximum in ((1, 1), (3, 1), (0, 2), (2, 4)):
            self.resolve(identities[index])
            self.assertEqual(maximum, self.series())

    def test_delayed_miss_cannot_join_successes_across_its_gap(self):
        identities = [self.launch() for unused in range(4)]
        for index, maximum in ((0, 1), (2, 1), (3, 2)):
            self.resolve(identities[index])
            self.assertEqual(maximum, self.series())
        self.resolve(identities[1], direct=None, outcome='miss', impact=None)
        self.assertEqual(2, self.series())
        self.resolve(self.launch())
        self.assertEqual(3, self.series())

    def test_new_verified_run_can_complete_with_an_earlier_shot_unresolved(self):
        earlier = self.launch()
        for unused in range(3):
            self.resolve(self.launch())
        self.assertIn(earlier, self.state.projectiles)
        self.assertEqual(3, self.series())
        self.resolve(earlier)
        self.assertEqual(4, self.series())

    def test_retried_launch_and_terminal_cannot_add_or_remove_credit(self):
        launch = projectile._launch()
        self.assertTrue(projectile._launch_authority(self.state, launch))
        self.assertTrue(projectile._launch_authority(self.state, launch))
        terminal = self.resolve('1:p:1:1')
        self.assertTrue(self.state.resolve_projectile(AUTHORITY, terminal))
        self.assertFalse(self.state.resolve_projectile(AUTHORITY, dict(
            terminal, direct=None, outcome='miss', impact=None)))
        self.assertTrue(projectile._launch_authority(self.state, launch))
        self.assertEqual(1, self.series())
        self.assertEqual(1, self.state._statistics_row('player', 1)['shots_fired'])
        self.assertEqual(1, self.state._statistics_row('player', 1)['shots_penetrated'])

    def test_ricochet_breaks_series_even_when_its_continuation_penetrates(self):
        self.resolve(self.launch())
        bounced = self.launch()
        ricochet = projectile._ricochet(bounced)
        self.assertTrue(self.state.ricochet_projectile(AUTHORITY, ricochet))
        self.assertTrue(self.state.ricochet_projectile(AUTHORITY, ricochet))
        self.resolve(bounced, base_checked_ms=100, resolved_time_ms=150,
                     checked_distance=20.0)
        self.resolve(self.launch())
        self.assertEqual(1, self.series())
        self.assertEqual(3, self.state._statistics_row('player', 1)['shots_penetrated'])

    def test_friendlies_wrecks_and_splash_are_not_penetrating_enemy_shots(self):
        for contact in ('friendly', 'wreck', 'splash'):
            with self.subTest(contact=contact):
                self.setUp()
                self.resolve(self.launch())
                if contact == 'friendly':
                    self.resolve(self.launch(), direct=projectile._effect(
                        target_id=3, damage=1, x=20.0), impact=[20.0, 1.0, 0.0],
                        checked_distance=20.0)
                elif contact == 'wreck':
                    self.state.players[4].alive = False
                    self.state.players[4].health = 0
                    self.resolve(self.launch(), direct=None, hit_vehicle=True,
                        wreck_hit={'target_kind': 'player', 'target_id': 4})
                else:
                    self.resolve(self.launch(is_he=True, splash_radius=20.0),
                        direct=None, splash=[projectile._effect(target_id=4,
                            damage=1, x=30.0, target_pose=(30.0, 1.0, 0.0))])
                self.resolve(self.launch())
                self.assertEqual(1, self.series())

    def test_zero_hp_damage_penetration_still_qualifies(self):
        # Penetrating an internal module need not subtract hull hit points.
        for unused in range(3):
            self.resolve(self.launch(), direct=projectile._effect(damage=0))
        self.assertEqual(3, self.series())
        self.assertEqual(0, self.state._statistics_row('player', 1)['damage_dealt'])

    def test_expired_or_rejected_terminal_does_not_hide_a_failed_shot(self):
        for outcome in ('expired', 'rejected'):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.resolve(self.launch())
                failed = self.launch(max_time_ms=100)
                if outcome == 'expired':
                    self.state.tick += 3
                    self.state.simulation_worker = None
                    self.state.bot_authority_id = None
                    self.assertEqual(1, self.state._expire_projectiles())
                    projectile._attach_worker_authority(self.state)
                else:
                    self.assertFalse(self.state.resolve_projectile(AUTHORITY,
                        projectile._resolve(failed, direct={'bad': True})))
                self.assertNotIn(failed, self.state.projectiles)
                self.resolve(self.launch())
                self.resolve(self.launch())
                self.assertEqual(2, self.series())

    def test_failed_effect_callback_cannot_manufacture_a_penetration(self):
        identity = self.launch()
        with mock.patch.object(self.state, '_apply_projectile_effect',
                               side_effect=RuntimeError('test failure')):
            self.resolve(identity)
        self.assertEqual(0, self.series())
        self.resolve(self.launch())
        self.assertEqual(1, self.series())

    def test_each_shooter_has_its_own_series_and_battle_reset_clears_it(self):
        for unused in range(3):
            self.resolve(self.launch())
            self.resolve(self.launch(3, origin=[20.0, 1.0, 0.0],
                                     velocity=[-100.0, 0.0, 0.0]),
                         direct=None, outcome='miss', impact=None)
        self.assertEqual(3, self.series())
        self.assertEqual(0, self.series(3))
        self.assertTrue(self.state._piercing_series_runs)
        self.state._reset_round()
        self.assertFalse(self.state._piercing_series_runs)
        self.assertEqual(0, self.series())

    def test_old_intervals_retire_once_no_inflight_shot_can_extend_them(self):
        for unused in range(8):
            self.resolve(self.launch())
            self.resolve(self.launch(), direct=None, outcome='miss', impact=None)
        self.assertEqual(1, self.series())
        self.assertEqual({}, self.state._piercing_series_runs)


class TD9ReceiptTests(unittest.TestCase):
    def check(self, receipt, quest, stage='main'):
        return policy._postbattle({'children': [('conditions', node(quest[stage]))]},
                                  policy._Facts(receipt, None))

    def wire(self, maximum=3):
        receipt = _receipt()
        receipt.update(type='battle_receipt', protocol=5)
        receipt['stats']['max_piercing_series'] = maximum
        receipt['public_results'][0]['stats']['max_piercing_series'] = maximum
        return receipt

    def test_all_four_real_td9_thresholds_and_separate_honour_requirements(self):
        for quest, kills in zip(DEFINITIONS, (1, 2, 3, 5)):
            threshold = quest['operation'] + 2
            receipt = self.wire(threshold - 1)
            receipt['stats'].update(piercings=threshold, kills=kills)
            self.assertEqual((False, set()), self.check(receipt, quest))
            receipt['stats']['max_piercing_series'] = threshold
            self.assertEqual((True, set()), self.check(receipt, quest))
            self.assertEqual((True, set()), self.check(receipt, quest, 'add'))
            receipt['stats']['kills'] = kills - 1
            self.assertEqual((True, set()), self.check(receipt, quest))
            self.assertEqual((False, set()), self.check(receipt, quest, 'add'))

    def test_legacy_receipts_stay_unknown_through_server_client_and_store(self):
        receipt = self.wire()
        for stats in (receipt['stats'], receipt['public_results'][0]['stats']):
            stats.pop('max_piercing_series')
        loaded = server._persisted_result_receipt(copy.deepcopy(receipt))
        self.assertTrue(client._valid_battle_receipt(loaded))
        normalized = postbattle_store._receipt(loaded)
        for row in (loaded, normalized):
            self.assertNotIn('max_piercing_series', row['stats'])
            self.assertNotIn('max_piercing_series', row['public_results'][0]['stats'])
            self.assertIsNone(self.check(row, DEFINITIONS[0])[0])
        # A known zero is a failed condition rather than missing evidence.
        self.assertEqual((False, set()), self.check(self.wire(0), DEFINITIONS[0]))

    def test_invalid_and_impossible_series_are_rejected_at_every_boundary(self):
        for value in (True, -1, 1.5, 3.0, '3', None, 5):
            for public_only in (False, True):
                with self.subTest(value=value, public_only=public_only):
                    receipt = self.wire()
                    receipt['public_results'][0]['stats']['max_piercing_series'] = value
                    if not public_only:
                        receipt['stats']['max_piercing_series'] = value
                    self.assertFalse(client._valid_battle_receipt(receipt))
                    with self.assertRaises(ValueError):
                        postbattle_store._receipt(copy.deepcopy(receipt))
                    with self.assertRaises(ValueError):
                        server._persisted_result_receipt(copy.deepcopy(receipt))
        receipt = self.wire(3)
        for stats in (receipt['stats'], receipt['public_results'][0]['stats']):
            stats['shots'] = 2
        self.assertFalse(client._valid_battle_receipt(receipt))
        with self.assertRaises(ValueError):
            postbattle_store._receipt(receipt)

    def test_client_keeps_existing_numeric_total_compatibility(self):
        for present in (False, True):
            receipt = self.wire()
            for stats in (receipt['stats'], receipt['public_results'][0]['stats']):
                stats.update(shots='8', piercings='4')
                if not present:
                    stats.pop('max_piercing_series')
            self.assertTrue(client._valid_battle_receipt(receipt))

    def test_real_projectiles_survive_restart_and_settle_td9_exactly_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state = projectile._state()
            state.receipt_state_path = path
            for index, player in state.players.items():
                player.account_key = str(index) * 32
            state._freeze_round_participants(tuple(state.players.values()))
            for unused in range(3):
                launch = projectile._launch()
                self.assertTrue(projectile._launch_authority(state, launch))
                self.assertTrue(state.resolve_projectile(AUTHORITY,
                    projectile._resolve('1:p:1:%s' % launch['shot_seq'],
                                        direct=projectile._effect(damage=1))))
            self.assertTrue(state._finish_battle(1, 'elimination'))
            receipt = _latest_receipt(state, state.players[1].account_key)
            self.assertEqual(3, receipt['stats']['max_piercing_series'])
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = server.BattleState(map_name='04_himmelsdorf', receipt_state_path=path)
            replay = _latest_receipt(restarted, state.players[1].account_key)
            self.assertEqual(receipt, replay)
            quest = definition('<results><key>inBattleMaxPiercingSeries</key>'
                '<greaterOrEqual>3</greaterOrEqual></results>',
                '<results><key>kills</key><greaterOrEqual>1</greaterOrEqual></results>', minimum=1)
            selection = {'personalMissionSelections': {'regular': [33]},
                         'personalMissionProgress': {}}
            vehicles = types.SimpleNamespace(VehicleDescr=lambda **unused:
                types.SimpleNamespace(type=types.SimpleNamespace(tags={'mediumTank'}, level=10)))
            completed = []
            store_path = str(Path(folder) / 'postbattle.json')
            store = postbattle_store.PostBattleStore(path=store_path)
            store._account_key = state.players[1].account_key
            def settle(row):
                result = policy.evaluate(selection, row, vehicles, lambda unused: quest)
                completed.append(result['completed'])
                selection['personalMissionProgress'].update(result['completed'])
                return {}
            store.set_progress_applier(settle)
            self.assertTrue(store.accept(replay))
            self.assertFalse(store.accept(replay))
            self.assertEqual([{'33': 1}], completed)
            reopened = postbattle_store.PostBattleStore(path=store_path)
            self.assertFalse(reopened.accept(replay))
            saved = reopened._pending[str(replay['arena_unique_id'])]
            self.assertEqual(3, saved['stats']['max_piercing_series'])


if __name__ == '__main__':
    unittest.main()
