"""Friendly-fire settlement and the reported repeated HE terminal."""
import copy
import types
import unittest
from unittest import mock

import test_port_0922_server_projectiles as shots
import test_port_0922_crew_settlement as settlement_fixture
import test_port_0922_postbattle as result_fixture
from gui.mods.offline_lan_0922 import friendly_fire, lan_client
from offline_rewards import compute_offline_rewards


class FriendlyFireProjectileTests(unittest.TestCase):
    def test_unverifiable_self_splash_cannot_reapply_the_direct_ally_hit(self):
        state = shots._state(players=3)
        ally = {
            'id': 7, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'health': 2050, 'max_health': 2050, 'alive': True,
            'display_health': 2050, 'x': 10.0, 'y': 0.0, 'z': 0.0,
            'critical': {}, 'combat_revision': 0,
            'combat_base_revision': 0, 'combat_ack_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0}
        state.bot_states[7] = ally
        self.assertTrue(shots._launch_authority(state, shots._launch(
            is_he=True, splash_radius=15.0)))
        state.players[1].effective_params.pop('critical', None)
        critical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        terminal = shots._resolve(
            '1:p:1:1', direct=shots._effect(target_kind='bot', target_id=7, damage=334),
            splash=[shots._effect(
                target_id=1, damage=50, target_pose=(0.0, 1.0, 0.0),
                critical=critical, critical_target_base_revision=0,
                critical_target_ack_seq=0, hull_damage=50,
                critical_delta={'devices': [{
                    'name': 'leftTrackHealth', 'hp_loss': 100.0}],
                    'crew_ko': [], 'ignite': False})])
        for unused in range(3):
            self.assertTrue(state.resolve_projectile(
                shots.SIMULATION_WORKER_AUTHORITY_ID, copy.deepcopy(terminal)))
        self.assertEqual(1716, ally['health'])
        self.assertEqual(950, state.players[1].health)
        self.assertEqual(334, state._statistics_row('player', 1)['team_damage'])
        self.assertEqual(0, state._statistics_row('player', 1)['team_kills'])
        self.assertNotIn('1:p:1:1', state.projectiles)
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(1, sum(event['kind'] == 'projectile_impact' for event in events))
        rejected = [event for event in events if event.get('critical_reject_reason')]
        self.assertEqual(['critical_profile'], [event['critical_reject_reason']
                                               for event in rejected])

    def test_post_hit_failure_is_contained_and_retries_do_not_charge_again(self):
        state = shots._state(players=3)
        self.assertTrue(shots._launch_authority(state, shots._launch(
            is_he=True, splash_radius=15.0)))
        terminal = shots._resolve(
            '1:p:1:1', direct=shots._effect(target_id=3, damage=334),
            splash=[shots._effect(target_id=2, damage=50,
                                  target_pose=(10.0, 1.0, 0.0))])
        apply = state._apply_projectile_effect

        def post_hit_failure(record, proposal):
            apply(record, proposal)
            if proposal['target_id'] == 3:
                raise ValueError('post-hit bookkeeping failure')

        with mock.patch.object(state, '_apply_projectile_effect', post_hit_failure):
            for unused in range(3):
                self.assertTrue(state.resolve_projectile(
                    shots.SIMULATION_WORKER_AUTHORITY_ID, terminal))
        self.assertEqual(666, state.players[3].health)
        self.assertEqual(950, state.players[2].health)
        self.assertEqual(334, state._statistics_row('player', 1)['team_damage'])
        self.assertEqual(50, state._statistics_row('player', 1)['damage_dealt'])
        self.assertIsNone(state.battle_result)

    def test_zero_damage_friendly_contact_does_not_record_damage_or_a_kill(self):
        state = shots._state(players=3)
        self.assertTrue(shots._launch_authority(state, shots._launch()))
        self.assertTrue(state.resolve_projectile(
            shots.SIMULATION_WORKER_AUTHORITY_ID,
            shots._resolve('1:p:1:1', direct=shots._effect(
                target_id=3, damage=0, shot_result=1))))
        row = state._statistics_row('player', 1)
        self.assertEqual((1, 0, 0), (row['team_hits'], row['team_damage'], row['team_kills']))
        self.assertEqual(1000, state.players[3].health)
        self.assertEqual(friendly_fire.facts(), state._friendly_fire_receipt(('player', 1)))

    def test_receipts_charge_actual_friendly_damage_and_compensate_the_victim(self):
        state = shots._state(players=3)
        for player_id, player in state.players.items():
            player.account_key = 'player-%d' % player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(shots._launch_authority(state, shots._launch()))
        self.assertTrue(state.resolve_projectile(
            shots.SIMULATION_WORKER_AUTHORITY_ID,
            shots._resolve('1:p:1:1', direct=shots._effect(target_id=3, damage=334))))
        state._finish_battle(2, 'battle_timeout')
        receipts = dict((row['player_id'], row) for row in state.result_receipts.values())
        offender, victim = receipts[1], receipts[3]
        self.assertTrue(lan_client._valid_battle_receipt(offender))
        self.assertTrue(lan_client._valid_battle_receipt(victim))
        self.assertEqual(334, offender['friendly_fire']['victims'][0]['damage'])
        self.assertEqual(334, victim['friendly_fire']['received_damage'])
        self.assertGreater(offender['friendly_fire']['xp_penalty'], 0)
        self.assertLess(offender['rewards']['xp'], victim['rewards']['xp'])
        self.assertEqual(0, offender['stats']['team_kills'])
        self.assertEqual(0, victim['friendly_fire']['xp_penalty'])
        self.assertEqual(0, offender['stats']['damage'])

    def test_self_damage_and_already_blue_victims_are_exempt_from_penalties(self):
        state = shots._state(players=3)
        state.players[3].team_killer = True
        state._record_damage(('player', 1), ('player', 3), 100, None)
        state._record_damage(('player', 1), ('player', 1), 100, None)
        state._record_frag('player', 1, 1, 'player', 3)
        self.assertEqual(friendly_fire.facts(), state._friendly_fire_receipt(('player', 1)))
        row = state._statistics_row('player', 1)
        self.assertEqual(0, row['team_damage_penalized'])
        self.assertEqual(0, row['team_killed_durability'])
        self.assertNotIn('xp_penalty', compute_offline_rewards(row, False))

    def test_friendly_kill_has_a_penalty_without_an_enemy_kill_reward(self):
        state = shots._state(players=3)
        state._record_frag('player', 1, 1, 'player', 3)
        row = state._statistics_row('player', 1)
        self.assertEqual(1000, row['team_killed_durability'])
        self.assertEqual((0, 1), (row['kills'], row['team_kills']))
        self.assertGreater(compute_offline_rewards(row, False)['xp_penalty'], 0)


class FriendlyFireEconomyTests(unittest.TestCase):
    def test_experience_penalty_reduces_both_xp_and_free_xp_and_is_bounded(self):
        clean = {'damage_dealt': 1000}
        for won in (False, True):
            with self.subTest(won=won):
                gross = compute_offline_rewards(clean, won, vehicle_tier=8)
                charged = compute_offline_rewards(dict(clean, team_damage_penalized=334),
                                                  won, vehicle_tier=8)
                self.assertEqual(100 if won else 67, charged['xp_penalty'])
                self.assertEqual(gross['xp'] - charged['xp_penalty'], charged['xp'])
                self.assertEqual(charged['xp'] * 5 // 100, charged['free_xp'])
                self.assertEqual(gross['credits'], charged['credits'])
                exhausted = compute_offline_rewards(
                    dict(clean, team_damage_penalized=100000), won, vehicle_tier=8)
                self.assertEqual((0, 0), (exhausted['xp'], exhausted['free_xp']))
                self.assertEqual(gross['xp'], exhausted['xp_penalty'])

    def test_each_victims_descriptor_prices_the_compensation(self):
        vehicles = types.SimpleNamespace(VehicleDescr=lambda typeName:
            types.SimpleNamespace(type=types.SimpleNamespace(repairCost={
                'ally:a': 3.0, 'ally:b': 1.5, 'own:tank': 2.0}[typeName])))
        facts = {'victims': [
            {'actor_kind': 'bot', 'actor_id': 1, 'vehicle': 'ally:a', 'damage': 334},
            {'actor_kind': 'player', 'actor_id': 2, 'vehicle': 'ally:b', 'damage': 1}],
            'received_damage': 10, 'xp_penalty': 0}
        self.assertEqual({'credits_out': 1004, 'credits_penalty': 100, 'credits_in': 20},
                         friendly_fire.price(facts, 'own:tank', vehicles))

    def test_wire_rejects_malformed_penalty_facts_but_accepts_old_receipts(self):
        receipt = result_fixture._receipt()
        self.assertTrue(lan_client._valid_battle_receipt(receipt))
        for field, value in (('received_damage', -1), ('xp_penalty', True),
                             ('victims', [{}]), ('victims', [0])):
            with self.subTest(field=field, value=value):
                receipt['friendly_fire'] = dict(friendly_fire.facts(), **{field: value})
                self.assertFalse(lan_client._valid_battle_receipt(receipt))


class FriendlyFireSettlementTests(unittest.TestCase):
    receipt = settlement_fixture.CrewSettlementTests.receipt
    packed_vehicle = settlement_fixture.CrewSettlementTests.packed_vehicle

    def setUp(self):
        settlement_fixture.CrewSettlementTests.setUp(self)
        original = self.vehicles.VehicleDescr

        def descriptor(compactDescr=None, typeName=None):
            value = original(compactDescr or b'fixture')
            value.type.repairCost = 3.0 if typeName == 'ally:heavy' else 2.0
            return value

        self.vehicles.VehicleDescr = descriptor
        self.snapshot['wallet']['credits'] = 2000

    def settle(self, receipt):
        return self.garage_store.apply_battle_crew_xp(
            self.snapshot, receipt['receipt_id'], 50001, receipt['rewards']['xp'], 1,
            tankmen_module=self.tankmen, vehicles_module=self.vehicles,
            rewards=receipt['rewards'], friendly_fire_facts=receipt.get('friendly_fire'),
            vehicle_type_name=receipt['vehicle'], health=receipt.get('health'),
            auto_settings=(2, 4, 8))

    def friendly_receipt(self):
        receipt = self.receipt()
        receipt['rewards']['credits'] = 100
        receipt['friendly_fire'] = {
            'victims': [{'actor_kind': 'bot', 'actor_id': 7,
                         'vehicle': 'ally:heavy', 'damage': 334}],
            'received_damage': 0, 'xp_penalty': 67}
        return receipt

    def test_negative_income_and_penalty_rows_survive_a_result_save_retry_and_restart(self):
        receipt = self.friendly_receipt()
        self.snapshot['earningsPercent'] = 200
        with mock.patch.object(self.result_store, '_save', side_effect=IOError('disk failure')):
            with self.assertRaises(IOError):
                self.result_store.accept(receipt)
        self.assertEqual(1098, self.snapshot['wallet']['credits'])
        banked = copy.deepcopy(self.snapshot)
        self.garage_store = self.stores.GarageStore(self.garage_path)
        self.snapshot['earningsPercent'] = 500
        self.assertTrue(self.result_store.accept(receipt))
        self.assertEqual(banked['wallet'], self.snapshot['wallet'])
        self.assertFalse(self.result_store.accept(receipt))
        restarted = self.results.PostBattleStore(self.result_path)
        vehicle = self.packed_vehicle(restarted, receipt)
        self.assertEqual(-902, vehicle['credits'])
        self.assertEqual((1002, 100), (vehicle['originalCreditsContributionOut'],
                                      vehicle['originalCreditsPenalty']))
        self.assertEqual(67, vehicle['originalXPPenalty'])
        self.assertEqual(1200, vehicle['xp'])
        self.assertEqual(60, vehicle['freeXP'])
        self.assertEqual(667, vehicle['originalXP'])
        self.assertEqual(600, vehicle['boosterXP'])
        self.assertIn(b'SUBCOEFF:originalXPPenalty:appliedPremiumXPFactor10',
                      vehicle['xpReplay'])
        self.assertIn(b'SUB:originalCreditsContributionOut', vehicle['creditsReplay'])
        with mock.patch.object(self.results, '_vehicle_type_compact_descr',
                               return_value=50001), mock.patch.object(
                self.results, '_arena_type_id', return_value=70001):
            self.assertEqual(-902, restarted.service_message_data(
                receipt['arena_unique_id'])['credits'])
        self.assertEqual(0, restarted.progress()['credits'])

    def test_garage_write_failure_leaves_all_balances_unchanged_then_retries_once(self):
        receipt = self.friendly_receipt()
        before = copy.deepcopy(self.snapshot)
        with mock.patch.object(self.garage_store, '_write_state', return_value=False):
            with self.assertRaises(RuntimeError):
                self.settle(receipt)
        self.assertEqual(before, self.snapshot)
        first = self.settle(receipt)
        self.assertEqual(998, self.snapshot['wallet']['credits'])
        self.assertEqual(-1002, first['awarded']['credits'])
        self.assertFalse(self.settle(receipt)['applied'])
        self.assertEqual(998, self.snapshot['wallet']['credits'])

    def test_fines_use_available_funds_before_auto_repair(self):
        receipt = self.friendly_receipt()
        receipt['rewards']['credits'] = 1000
        receipt['health'] = 900
        self.snapshot['wallet']['credits'] = 0
        self.snapshot['vehicles'][0]['settings'] = 2
        result = self.settle(receipt)
        self.assertEqual(0, self.snapshot['wallet']['credits'])
        self.assertEqual((200, 900), self.snapshot['vehicles'][0]['repair'])
        self.assertEqual(0, result['service_costs']['repair_credits'])
        self.assertEqual(1000, result['friendly_fire_costs']['credits_out'])
        self.assertEqual(0, result['friendly_fire_costs']['credits_penalty'])
        self.assertEqual(0, result['awarded']['credits'])

    def test_victim_compensation_is_unscaled_and_paid_once(self):
        receipt = self.receipt()
        receipt['rewards']['credits'] = 100
        receipt['friendly_fire'] = dict(friendly_fire.facts(), received_damage=334)
        self.snapshot['earningsPercent'] = 300
        result = self.settle(receipt)
        self.assertEqual(2968, self.snapshot['wallet']['credits'])
        self.assertEqual(668, result['friendly_fire_costs']['credits_in'])
        self.assertEqual(968, result['awarded']['credits'])
        self.assertEqual(0, result['friendly_fire_costs']['credits_out'])
        self.assertFalse(self.settle(receipt)['applied'])
        self.assertEqual(2968, self.snapshot['wallet']['credits'])

    def test_unknown_native_price_does_not_commit_a_partial_settlement(self):
        receipt = self.friendly_receipt()
        before = copy.deepcopy(self.snapshot)
        self.vehicles.VehicleDescr = mock.Mock(side_effect=KeyError('missing descriptor'))
        with self.assertRaises(KeyError):
            self.settle(receipt)
        self.assertEqual(before, self.snapshot)

    def test_damaged_cost_breakdown_keeps_the_durable_duplicate_marker(self):
        receipt = self.friendly_receipt()
        self.settle(receipt)
        raw = dict(self.garage_store._battle_receipts[0],
                   friendly_fire_costs={'credits_out': 'damaged'})
        rows = self.stores.GarageStore._validated_battle_receipts([raw])
        self.assertEqual(receipt['receipt_id'], rows[0]['receipt_id'])
        self.assertEqual(-1002, rows[0]['awarded']['credits'])


if __name__ == '__main__':
    unittest.main()
