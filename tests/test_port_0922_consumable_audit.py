"""Regression coverage for consumable settlement and rejected trigger ordering.

Uses the real server, client encoder and garage transactions without BigWorld.
Equipment magnitudes are explicit test fixtures, never replacement game data.
"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
import lan_battle_server as server
from gui.mods.offline_lan_0922 import equipment_mechanics as equipment
from gui.mods.offline_lan_0922 import lan_client
from gui.mods.offline_lan_0922.account_rpc import postbattle_store
from gui.mods.offline_lan_0922.account_rpc.garage import GarageState, GarageError, EQUIPMENT_ITEM_TYPE
from gui.mods.offline_lan_0922.account_rpc.garage_store import GarageStore, _settle_automatically


def item(name, item_id, **values):
    data = dict(name=name, id=(0, item_id), compactDescr=(item_id << 8) | 11,
                tags=(), reuseCount=0, cooldownSeconds=0)
    data.update(values)
    return equipment.EquipmentState(equipment.project_equipment(
        types.SimpleNamespace(**data)))


def governor():
    return item('removedRpmLimiter', 12, tags=('trigger',),
                enginePowerFactor=1.1, engineHpLossPerSecond=1.5)


def food():
    return item('ration', 10, crewLevelIncrease=10)


def fuel():
    return item('fuel', 20, enginePowerFactor=1.05)


def medkit():
    return item('smallMedkit', 2, tags=('medkit',),
                reuseCount=-1, cooldownSeconds=90)


def battle_state(items=None, mode='regular'):
    battle = server.BattleState()
    battle.client_build = server.CLIENT_BUILD_0922
    battle.phase, battle.tick, battle.battle_mode = 'battle', 600, mode
    player = server.Player(player_id=1, name='Audit', conn=None, address=None)
    player.connected = player.participating = player.alive = True
    player.health = player.max_health = 100
    player.team, player.account_key = 1, 'audit-account'
    player.vehicle = 'ussr:R11_MS-1'
    player.equipment_states = list(items if items is not None else [governor()])
    player.effective_params = {'critical': {
        'devices': [{'name': 'engineHealth', 'max_hp': 100, 'regen_hp': 50}],
        'activation_targets': [{'index': 10, 'name': 'commander'}]}}
    battle.players[1] = player
    battle._freeze_round_participants([player])
    return battle, player


def intent(sequence, item_id=12, selected=None, extra=1, active=True):
    return dict(type='equipment_intent', round_id=1, intent_seq=sequence,
                equipment_id=item_id, activation_code=(extra << 16) + item_id,
                selected=selected, requested_active=active)


def finish(battle):
    assert battle._finish_battle(1, 'team_eliminated')
    raw = list(battle.result_receipts.values())[0]
    assert lan_client._valid_battle_receipt(raw)
    canonical = postbattle_store._receipt(json.loads(json.dumps(raw)))
    assert canonical['equipment_used'] == raw['equipment_used']
    return raw


def garage_snapshot(items, spare=0):
    cds = [value.contract['compactDescr'] for value in items]
    layout = cds + [0] * (3 - len(cds))
    return {
        'vehicles': [{'id': 1, 'vehicleTypeCompactDescr': 42,
                      'eqs': list(layout), 'eqsLayout': list(layout),
                      'inventoryItems': {EQUIPMENT_ITEM_TYPE: {cd: 1 for cd in cds}}}],
        'inventoryItems': {EQUIPMENT_ITEM_TYPE: {cd: 1 + spare for cd in cds}},
        'wallet': {'credits': 100000, 'gold': 0, 'freeXP': 0, 'crystal': 0},
    }


class ConsumableSettlementTests(unittest.TestCase):
    def test_governor_toggles_do_not_consume_an_item_when_surviving(self):
        for turns in (0, 1, 2, 3):
            with self.subTest(turns=turns):
                mounted = governor()
                battle, player = battle_state([mounted])
                for index in range(turns):
                    active = index % 2 == 0
                    message = intent(index + 1, extra=int(active), active=active)
                    self.assertTrue(battle.submit_equipment_intent(1, message))
                    revision = player.equipment_revision
                    self.assertTrue(battle.submit_equipment_intent(1, message))
                    self.assertEqual(revision, player.equipment_revision)
                self.assertEqual(1, mounted.uses_left)
                receipt = finish(battle)
                self.assertEqual([], receipt['equipment_used'])
                garage = GarageState(garage_snapshot([mounted]))
                self.assertEqual([], garage.settle_battle_consumables(42, receipt['equipment_used']))
                self.assertEqual(mounted.contract['compactDescr'], garage.snapshot()['vehicles'][0]['eqs'][0])

    def test_governor_death_charge_uses_final_switch_and_settles_only_once(self):
        for mode in ('regular', 'training'):
            for turns in (0, 1, 2, 3):
                for reason in (3, 7):
                    with self.subTest(mode=mode, turns=turns, reason=reason):
                        mounted = governor()
                        battle, player = battle_state([mounted], mode)
                        for index in range(turns):
                            active = index % 2 == 0
                            self.assertTrue(battle.submit_equipment_intent(1,
                                intent(index + 1, extra=int(active), active=active)))
                        self.assertTrue(battle._commit_player_environment_damage(
                            player, player.health, reason))
                        expected = [mounted.contract['compactDescr']] if turns % 2 else []
                        # Death and retries cannot change the sampled switch.
                        self.assertTrue(battle.submit_equipment_intent(1,
                            intent(turns + 1, extra=0, active=False)))
                        self.assertEqual('vehicle_not_alive',
                            player.equipment_intent_result['reason'])
                        battle._record_vehicle_end('player', 1)
                        receipt = finish(battle)
                        self.assertEqual(expected, receipt['equipment_used'])
                        snapshot = garage_snapshot([mounted], spare=1)
                        with tempfile.TemporaryDirectory() as directory:
                            path = str(Path(directory) / 'garage.json')
                            store = GarageStore(path)
                            kwargs = dict(training=True, equipment_used=expected)
                            result = store.apply_battle_crew_xp(
                                snapshot, receipt['receipt_id'], 42, 0, 0, **kwargs)
                            self.assertTrue(result['applied'])
                            self.assertEqual([], result['refused'])
                            self.assertEqual(0 if expected else mounted.contract['compactDescr'],
                                snapshot['vehicles'][0]['eqs'][0])
                            before = copy.deepcopy(snapshot)
                            self.assertFalse(GarageStore(path).apply_battle_crew_xp(
                                snapshot, receipt['receipt_id'], 42, 0, 0, **kwargs)['applied'])
                            self.assertEqual(before, snapshot)

    def test_live_departure_is_not_an_active_governor_death(self):
        battle, player = battle_state()
        self.assertTrue(battle.submit_equipment_intent(1, intent(1)))
        # The live-departure path freezes lifetime before clearing the actor.
        battle._record_vehicle_end('player', 1)
        player.alive = False
        battle._record_vehicle_end('player', 1)
        self.assertEqual([], finish(battle)['equipment_used'])

    def test_destroyed_active_governor_resupplies_from_stock_before_purchase(self):
        for spare in (0, 1):
            with self.subTest(spare=spare):
                mounted = governor()
                battle, player = battle_state([mounted])
                self.assertTrue(battle.submit_equipment_intent(1, intent(1)))
                self.assertTrue(battle._commit_player_environment_damage(player, 100, 3))
                receipt = finish(battle)
                snapshot = garage_snapshot([mounted], spare=spare)
                cd = mounted.contract['compactDescr']
                snapshot['vehicles'][0]['settings'] = 4
                snapshot['shopItemPrices'] = {cd: {'credits': 3000}}
                descriptors = types.SimpleNamespace(getItemByCompactDescr=lambda cd:
                    types.SimpleNamespace(equipmentType=0))
                garage = GarageState(snapshot, vehicles_module=descriptors)
                self.assertEqual([cd], garage.settle_battle_consumables(42, receipt['equipment_used']))
                costs = _settle_automatically(garage, 1, (1, 2, 4), GarageError)
                self.assertEqual(3000 if spare == 0 else 0, costs['equipment_credits'])
                self.assertEqual(cd, garage.snapshot()['vehicles'][0]['eqs'][0])

    def test_passive_supplies_are_consumed_without_activation_in_both_modes(self):
        for mode in ('regular', 'training'):
            with self.subTest(mode=mode):
                mounted = [food(), fuel()]
                battle, player = battle_state(mounted, mode)
                self.assertEqual(10, equipment.passive_effects(mounted)['crewLevelIncrease'])
                self.assertEqual(1.05, equipment.passive_effects(mounted)['enginePowerFactor'])
                self.assertEqual(0, player.equipment_intent_seq)
                receipt = finish(battle)
                expected = sorted(value.contract['compactDescr'] for value in mounted)
                self.assertEqual(expected, receipt['equipment_used'])
                garage = GarageState(garage_snapshot(mounted))
                self.assertEqual(expected, sorted(garage.settle_battle_consumables(42, receipt['equipment_used'])))
                self.assertEqual([0, 0, 0], garage.snapshot()['vehicles'][0]['eqs'])

    def test_all_supported_passive_descriptor_variants_share_the_policy(self):
        for value in (food(), fuel(), item('oil', 21, enginePowerFactor=1.1),
                      item('turretFuel', 22, turretRotationSpeedFactor=1.05),
                      item('anotherNationRation', 23, crewLevelIncrease=10)):
            with self.subTest(name=value.contract['name']):
                battle, _ = battle_state([value])
                self.assertEqual([value.contract['compactDescr']], finish(battle)['equipment_used'])

    def test_unused_kits_and_automatic_extinguisher_are_not_consumed(self):
        mounted = [medkit(), item('largeRepairkit', 5, tags=('repairkit',), repairAll=True),
                   item('autoExtinguishers', 1, autoactivate=True, reuseCount=-1, cooldownSeconds=90)]
        battle, _ = battle_state(mounted)
        self.assertEqual([], finish(battle)['equipment_used'])

    def test_unknown_passive_item_is_not_assumed_consumable(self):
        battle, _ = battle_state([item('unknownPassive', 30)])
        self.assertEqual([], finish(battle)['equipment_used'])

    def test_reusable_medkit_is_consumed_once_across_uses_and_retries(self):
        med = medkit()
        battle, player = battle_state([med])
        for sequence in (1, 2):
            player.stun_end_server_time_ms = battle._server_time_ms() + 20000
            message = intent(sequence, 2, 'commander', 10, None)
            self.assertTrue(battle.submit_equipment_intent(1, message))
            self.assertTrue(player.equipment_intent_result['accepted'])
            self.assertEqual(0, player.stun_end_server_time_ms)
            revision = player.equipment_revision
            self.assertTrue(battle.submit_equipment_intent(1, message))
            self.assertEqual(revision, player.equipment_revision)
            battle.tick += int(91 * server.TICK_HZ)
        self.assertEqual([med.contract['compactDescr']], finish(battle)['equipment_used'])

    def test_automatic_extinguisher_is_still_settled_once(self):
        auto = item('autoExtinguishers', 1, autoactivate=True, reuseCount=-1, cooldownSeconds=90)
        battle, player = battle_state([auto])
        player.critical = {'fire': True}
        def extinguish(participant, payload):
            participant.critical = {'fire': False}
        with mock.patch.object(battle, '_commit_player_critical_progress', side_effect=extinguish), \
                mock.patch.object(server.player_critical_mechanics, 'apply_equipment', return_value={}), \
                mock.patch.object(server.player_critical_mechanics, 'advance_critical', return_value=None):
            self.assertEqual(1, battle._tick_player_critical(0.1))
            self.assertEqual(0, battle._tick_player_critical(0.1))
        self.assertEqual([auto.contract['compactDescr']], finish(battle)['equipment_used'])

    def test_consumption_uses_battle_start_loadout_after_disconnect(self):
        mounted = food()
        battle, player = battle_state([mounted])
        player.equipment_states = [governor()]
        del battle.players[1]
        self.assertEqual([mounted.contract['compactDescr']], finish(battle)['equipment_used'])

    def test_loading_cancellation_and_infrastructure_failure_do_not_bill(self):
        battle, _ = battle_state([food()])
        battle.phase = 'loading'
        battle._reset_round()
        self.assertEqual({}, battle.result_receipts)
        self.assertEqual({}, battle.vehicle_statistics)
        battle, _ = battle_state([food()])
        self.assertTrue(battle._finish_battle(0, 'worker_disconnected', record_receipts=False))
        self.assertEqual({}, battle.result_receipts)

    def test_auto_resupply_uses_depot_first_and_keeps_a_surviving_governor(self):
        for spare in (0, 1):
            with self.subTest(spare=spare):
                mounted = [food(), fuel(), governor()]
                battle, _ = battle_state(mounted)
                receipt = finish(battle)
                snapshot = garage_snapshot(mounted, spare=spare)
                snapshot['vehicles'][0]['settings'] = 4
                snapshot['shopItemPrices'] = {
                    value.contract['compactDescr']: {'credits': (index + 1) * 100}
                    for index, value in enumerate(mounted)}
                descriptors = types.SimpleNamespace(getItemByCompactDescr=lambda cd:
                    types.SimpleNamespace(equipmentType=0))
                garage = GarageState(snapshot, vehicles_module=descriptors)
                garage.settle_battle_consumables(42, receipt['equipment_used'])
                costs = _settle_automatically(garage, 1, (1, 2, 4), GarageError)
                expected_cost = 300 if spare == 0 else 0
                self.assertEqual(expected_cost, costs['equipment_credits'])
                self.assertEqual(100000 - expected_cost, garage.snapshot()['wallet']['credits'])
                self.assertEqual([value.contract['compactDescr'] for value in mounted],
                                 garage.snapshot()['vehicles'][0]['eqs'])

    def test_an_early_quit_still_settles_the_frozen_consumables_once(self):
        mounted = food()
        battle, player = battle_state([mounted])
        battle.round_participants[player.account_key]['premature_leave'] = True
        player.connected = False
        receipt = finish(battle)
        self.assertTrue(receipt['premature_leave'])
        self.assertEqual([mounted.contract['compactDescr']], receipt['equipment_used'])
        self.assertFalse(battle._finish_battle(1, 'team_eliminated'))
        self.assertEqual(1, len(battle.result_receipts))

    def test_duplicate_receipt_does_not_consume_restored_stock_again(self):
        mounted = [food(), fuel(), governor()]
        battle, _ = battle_state(mounted)
        receipt = finish(battle)
        snapshot = garage_snapshot(mounted, spare=1)
        with tempfile.TemporaryDirectory() as directory:
            store = GarageStore(str(Path(directory) / 'garage.json'))
            kwargs = dict(training=True, equipment_used=receipt['equipment_used'])
            first = store.apply_battle_crew_xp(snapshot, receipt['receipt_id'], 42, 0, 0, **kwargs)
            self.assertTrue(first['applied'])
            self.assertEqual([], first['refused'])
            self.assertEqual([0, 0, mounted[2].contract['compactDescr']], snapshot['vehicles'][0]['eqs'])
            # Model a player filling the emptied slots from remaining depot stock.
            snapshot['vehicles'][0]['eqs'] = [value.contract['compactDescr'] for value in mounted]
            before = copy.deepcopy(snapshot)
            for active_store in (store, GarageStore(str(Path(directory) / 'garage.json'))):
                replay = active_store.apply_battle_crew_xp(snapshot, receipt['receipt_id'], 42, 0, 0, **kwargs)
                self.assertFalse(replay['applied'])
                self.assertEqual(before, snapshot)


class GovernorWearTests(unittest.TestCase):
    def setUp(self):
        self.battle, self.player = battle_state()
        self.player.effective_params['physics'] = {'speedFwd': 20.0, 'speedBwd': 5.0}
        self.player.effective_params['critical']['devices'][0].update(max_hp=60, regen_hp=30)
        self.assertTrue(self.battle.submit_equipment_intent(1, intent(1)))

    def hp(self):
        rows = self.player.critical.get('devices') or []
        return next((row['hp'] for row in rows if row['name'] == 'engineHealth'), 60.0)

    def step(self, dt):
        self.battle.tick += int(round(dt * server.TICK_HZ))
        self.battle._tick_player_critical(dt)

    def test_rate_uses_one_second_pulses_and_matches_twenty_second_example(self):
        for _ in range(3):
            self.step(0.25)
            self.assertEqual(60.0, self.hp())
        self.step(0.25)
        self.assertEqual(58.5, self.hp())
        for _ in range(190):
            self.step(0.1)
        self.assertEqual(30.0, self.hp())

    def test_only_speed_magnitude_below_half_base_forward_limit_wears_engine(self):
        for speed in (10.0, 12.0, 20.0, -10.0, -12.0):
            with self.subTest(speed=speed):
                self.player.speed = speed
                self.step(2.0)
                self.assertEqual(60.0, self.hp())
        for speed in (9.99, -9.99, 0.0):
            with self.subTest(speed=speed):
                before = self.hp()
                self.player.speed = speed
                self.step(1.0)
                self.assertEqual(before - 1.5, self.hp())

    def test_disabled_and_cruising_do_not_carry_old_partial_wear(self):
        self.step(0.75)
        self.assertTrue(self.battle.submit_equipment_intent(1,
            intent(2, extra=0, active=False)))
        self.assertEqual(0.0, self.player.rpm_damage_elapsed)
        self.step(5.0)
        self.assertEqual(60.0, self.hp())
        self.assertTrue(self.battle.submit_equipment_intent(1, intent(3)))
        self.step(0.75)
        self.assertEqual(60.0, self.hp())
        self.player.speed = 10.0
        self.step(0.1)
        self.player.speed = 0.0
        self.step(0.75)
        self.assertEqual(60.0, self.hp())
        self.step(0.25)
        self.assertEqual(58.5, self.hp())

    def test_late_tick_preserves_complete_seconds_without_hull_damage(self):
        self.step(2.25)
        self.assertEqual(57.0, self.hp())
        self.assertEqual(0.25, self.player.rpm_damage_elapsed)
        self.assertEqual(100, self.player.health)

    def test_kv5_engine_pool_needs_two_minutes_to_turn_yellow(self):
        from effective_params_fixture import effective_params
        params = effective_params()
        params['equipment'] = [governor().contract]
        # Installed #1513 M500 values, not the earlier 60 HP example.
        params['critical']['devices'][0].update(max_hp=360, regen_hp=180)
        params['critical']['activation_targets'] = []
        self.player.effective_params = params
        self.assertTrue(self.battle._install_player_equipments(self.player))
        self.assertTrue(self.battle.submit_equipment_intent(1, intent(1)))
        for _ in range(120 * int(server.TICK_HZ)):
            self.step(1.0 / server.TICK_HZ)
        row = self.player.critical['devices'][0]
        self.assertEqual(180, row['hp'])
        self.assertEqual('critical', row['state'])
        self.assertGreater(self.player.critical_revision, 0)
        snapshot = self.battle._public_player(self.player)
        self.assertEqual(row, snapshot['critical']['devices'][0])


class EquipmentRequestOrderingTests(unittest.TestCase):
    def test_bad_target_is_a_terminal_rejection_not_a_sequence_hole(self):
        battle, player = battle_state()
        self.assertTrue(battle.submit_equipment_intent(1, intent(1, selected='unknown_extra')))
        self.assertEqual({'intent_seq': 1, 'accepted': False, 'reason': 'invalid_equipment_target'},
                         player.equipment_intent_result)
        self.assertFalse(player.equipment_states[0].active)
        self.assertTrue(battle.submit_equipment_intent(1, intent(2)))
        self.assertTrue(player.equipment_intent_result['accepted'])
        self.assertTrue(player.equipment_states[0].active)

    def test_every_identifiable_bad_payload_leaves_next_request_usable(self):
        mutations = [dict(selected=[]), dict(selected=''), dict(selected='x' * 65),
                     dict(requested_active=1), dict(equipment_id=-1),
                     dict(equipment_id=True), dict(activation_code=None),
                     dict(activation_code=65549), dict(extra_field=True)]
        for changes in mutations + [None]:
            with self.subTest(changes=changes):
                battle, player = battle_state()
                bad = intent(1)
                if changes is None:
                    del bad['selected']
                else:
                    bad.update(changes)
                revision = player.equipment_revision
                self.assertTrue(battle.submit_equipment_intent(1, bad))
                self.assertEqual(1, player.equipment_intent_seq)
                self.assertFalse(player.equipment_intent_result['accepted'])
                self.assertTrue(player.equipment_intent_result['reason'])
                self.assertEqual(revision, player.equipment_revision)
                self.assertTrue(lan_client._valid_player_equipment_contract(battle._public_player(player)))
                self.assertTrue(battle.submit_equipment_intent(1, bad))
                self.assertTrue(battle.submit_equipment_intent(1, intent(2)))
                self.assertTrue(player.equipment_intent_result['accepted'])

    def test_wrong_round_type_identity_and_missing_sequence_do_not_advance(self):
        for changes in (dict(round_id=2), dict(type='input'), dict(intent_seq=0),
                        dict(intent_seq=True), dict(intent_seq=None)):
            with self.subTest(changes=changes):
                battle, player = battle_state()
                bad = intent(1)
                bad.update(changes)
                self.assertFalse(battle.submit_equipment_intent(1, bad))
                self.assertEqual(0, player.equipment_intent_seq)
                self.assertTrue(battle.submit_equipment_intent(1, intent(1)))
        battle, player = battle_state()
        self.assertFalse(battle.submit_equipment_intent(999, intent(1)))
        self.assertFalse(battle.submit_equipment_intent(1, None))
        self.assertEqual(0, player.equipment_intent_seq)

    def test_gap_conflict_and_duplicate_do_not_execute_another_operation(self):
        battle, player = battle_state()
        self.assertFalse(battle.submit_equipment_intent(1, intent(2)))
        self.assertTrue(battle.submit_equipment_intent(1, intent(1)))
        revision = player.equipment_revision
        self.assertFalse(battle.submit_equipment_intent(1, intent(1, extra=0, active=False)))
        self.assertTrue(battle.submit_equipment_intent(1, intent(1)))
        self.assertEqual(revision, player.equipment_revision)
        self.assertTrue(player.equipment_states[0].active)
        self.assertTrue(battle.submit_equipment_intent(1, intent(2, extra=0, active=False)))
        self.assertFalse(player.equipment_states[0].active)

    def test_normal_business_rejection_still_recovers(self):
        battle, player = battle_state()
        self.assertTrue(battle.submit_equipment_intent(1, intent(1, item_id=999)))
        self.assertEqual('equipment_not_mounted', player.equipment_intent_result['reason'])
        self.assertTrue(battle.submit_equipment_intent(1, intent(2)))
        self.assertTrue(player.equipment_intent_result['accepted'])

    def test_client_rejects_unknown_target_before_allocating_sequence(self):
        client = lan_client.LANClient('127.0.0.1', 28782, 'Audit', 'ussr:R11_MS-1')
        client.ready, client.phase, client.player_id, client.round_id = True, 'battle', 1, 1
        client.is_bot_authority = lambda: False
        sent = []
        client._send = lambda message: sent.append(copy.deepcopy(message)) or True
        self.assertIsNone(client.send_equipment_intent(12, 65548, 'unknown_extra', True))
        self.assertEqual([], sent)
        self.assertEqual(0, client._equipment_intent_seq)
        self.assertEqual(1, client.send_equipment_intent(12, 65548, None, True))
        battle, player = battle_state()
        self.assertTrue(battle.submit_equipment_intent(1, sent[0]))
        self.assertTrue(player.equipment_intent_result['accepted'])

    def test_client_and_server_share_activation_target_names(self):
        self.assertEqual(server.CRITICAL_DEVICE_NAMES, equipment.ACTIVATION_DEVICE_NAMES)
        self.assertEqual(server.CRITICAL_CREW_NAMES, equipment.ACTIVATION_CREW_NAMES)

    def test_failed_send_does_not_allocate_sequence(self):
        client = lan_client.LANClient('127.0.0.1', 28782, 'Audit', 'ussr:R11_MS-1')
        client.ready, client.phase = True, 'battle'
        client.is_bot_authority = lambda: False
        client._send = lambda message: False
        self.assertIsNone(client.send_equipment_intent(12, 65548, None, True))
        self.assertEqual(0, client._equipment_intent_seq)


if __name__ == '__main__':
    unittest.main()
