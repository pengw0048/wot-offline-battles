"""Regression coverage for bond purchases, settlement and crew directives."""

import copy
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from test_port_0922_garage import SNAPSHOT, _modules, _load, _request_modules
from test_port_0922_postbattle import _receipt, _packed_vehicle, _Socket
from test_port_0922_battle_runtime import (
    _runtime, _Client, _Descriptor, _effective_params_snapshot)
from effective_params_fixture import effective_params
from gui.mods.offline_lan_0922 import battle_bonds, loadout, price_catalogue
from gui.mods.offline_lan_0922 import effective_params as wire
from gui.mods.offline_lan_0922 import vehicle_blacklist
from gui.mods.offline_lan_0922.account_rpc import economy, postbattle_store
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
import lan_battle_server as server


class BondsAndDirectivesTests(unittest.TestCase):
    def test_price_baker_preserves_crystal_through_parse_render_and_load(self):
        with mock.patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'tools')] + sys.path):
            import bake_prices_0922
            import vehicle_prices
            import packed_xml
        price = types.SimpleNamespace(value=types.SimpleNamespace(value=b'5000'),
                                      children=[(b'crystal', object())])
        section = types.SimpleNamespace(children=[(b'price', types.SimpleNamespace(
            value_type=packed_xml.TYPE_ELEMENT, value=price))])
        parsed = vehicle_prices.read_price(section)
        source = bake_prices_0922.render('0.9.22.0.1', 1513, {}, {}, {},
                                        {'deluxRammer': parsed})
        namespace = {}
        exec(source, namespace)
        self.assertEqual({'crystal': 5000}, namespace['money'](
            namespace['ARTEFACT_PRICES']['deluxRammer']))

    def test_teams_draw_different_models_from_the_same_valid_tier_and_class(self):
        names = ['ussr:one', 'ussr:two',
                 sorted(vehicle_blacklist.UNUSABLE_VEHICLES)[0]]
        entries = {index: types.SimpleNamespace(
            name=name, level=8, tags=('heavyTank',))
                   for index, name in enumerate(names)}
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(AVAILABLE_NAMES=('ussr',), INDICES={'ussr': 0}),
            vehicles=types.SimpleNamespace(g_list=types.SimpleNamespace(getList=lambda unused: entries)))
        descriptor = types.SimpleNamespace(type=entries[0])
        start = {'round_id': 7, 'map': '01_karelia', 'players': [
            {'id': 1, 'team': 1, 'slot': 0, 'vehicle': names[0]},
            {'id': 2, 'team': 2, 'slot': 0, 'vehicle': names[0]}],
            'bots': [{'id': team * 100 + slot, 'team': team, 'slot': slot}
                     for team in (1, 2) for slot in range(1, 15)]}
        results = []
        for player_id in (1, 2):
            battle = BattleRuntime(runtime)
            battle._config = {'vehicle': names[0]}
            battle.client = types.SimpleNamespace(player_id=player_id, team=player_id)
            battle._start_message = start
            battle._resolve_descriptor = lambda unused: descriptor
            self.assertTrue(battle._prepare_bot_vehicle_assignments(descriptor))
            results.append(battle._bot_vehicle_assignments)
        self.assertEqual(results[0], results[1])
        self.assertEqual(set(names[:2]), set(results[0].values()))
        self.assertNotEqual([results[0][1, slot] for slot in range(1, 15)],
                            [results[0][2, slot] for slot in range(1, 15)])

    def test_native_crew_effects_reach_bloom_and_ammo_rack_without_shared_state(self):
        class Crew:
            _skillProcessors = {}
        crew = Crew()
        factors = loadout._collect_battle_crew_factors(crew)
        config = types.SimpleNamespace(shotDispersionFactorPerLevel=0.001,
                                      ammoBayHealthFactor=1.375)
        crew._skillProcessors['driver_smoothDriving'](crew, 0, 100, 0, True, False, config)
        crew._skillProcessors['gunner_smoothTurret'](crew, 1, 100, 0, True, False, config)
        crew._skillProcessors['loader_pedant'](crew, 2, 100, 0, True, False, config)
        descriptor = types.SimpleNamespace(optionalDevices=[types.SimpleNamespace(
            name='deluxAimingStabilizer')],
            miscAttrs={'additiveShotDispersionFactor': 0.75})
        factors['additiveShotDispersionFactor'] = 0.933
        values = loadout.modifiers(descriptor, factors=factors)
        self.assertAlmostEqual(0.75 * 0.933 * 0.9, values['bloom_move_factor'])
        self.assertAlmostEqual(0.75 * 0.933 * 0.9, values['bloom_turret_factor'])
        self.assertEqual(1.375, factors['offline/ammoBayHealth'])
        self.assertEqual({}, Crew._skillProcessors)
        inactive = loadout._collect_battle_crew_factors(Crew())
        self.assertEqual(1.0, inactive['offline/moveBloom'])

    def _garage(self, bonds=100):
        garage = _load('garage')
        vehicles, tankmen = _modules()
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 1000000, 'gold': 100, 'freeXP': 0,
                              'crystal': bonds}
        snapshot['shopItemPrices'].update({11003: {'crystal': 6},
                                           9002: {'crystal': 5000}})
        state = garage.GarageState(snapshot, vehicles_module=vehicles,
                                   tankmen_module=tankmen)
        return garage, state, vehicles, tankmen

    def test_all_six_improved_devices_and_fifteen_directives_cost_bonds(self):
        prices = {name: price_catalogue.money(value)
                  for name, value in price_catalogue.ARTEFACT_PRICES.items()
                  if name.startswith('delux') or name.endswith('BattleBooster')}
        self.assertEqual(21, len(prices))
        self.assertTrue(all(set(value) == {'crystal'} for value in prices.values()))
        self.assertEqual({'crystal': 3000}, prices['deluxToolbox'])
        self.assertEqual({'crystal': 4000}, prices['deluxCoatedOptics'])
        self.assertEqual({'crystal': 5000}, prices['deluxRammer'])
        self.assertEqual({'crystal': 6}, prices['sixthSenseBattleBooster'])

    def test_medal_bonds_follow_tier_boundaries_and_do_not_duplicate(self):
        self.assertEqual({}, battle_bonds.medal_rewards(['warrior'], 3))
        self.assertEqual({'warrior': 1}, battle_bonds.medal_rewards(['warrior'], 4))
        self.assertEqual({'warrior': 3, 'mainGun': 2},
                         battle_bonds.medal_rewards(['warrior', 'warrior', 'mainGun'], 10))
        self.assertEqual(5, battle_bonds.medal_reward('medalHalonen', 8))
        self.assertEqual(0, battle_bonds.medal_reward('medalHalonen', 9))
        self.assertEqual(0, battle_bonds.medal_reward('medalLafayettePool', 4))
        self.assertEqual(5, battle_bonds.medal_reward('medalLafayettePool', 5))
        self.assertEqual(0, battle_bonds.medal_reward('markOfMastery', 10))
        rewards = economy.scale_rewards({'credits': 100, 'xp': 200,
                                         'free_xp': 10, 'crystal': 5}, 300, 400)
        self.assertEqual(5, rewards['crystal'])

    def test_bonds_cannot_be_replaced_by_credits_or_gold(self):
        garage, state, unused_vehicles, unused_tankmen = self._garage(5)
        before = state.snapshot()
        with self.assertRaises(garage.GarageError):
            state.set_layouts(9, None, 1, [0, 0, 0, 0, 0, 0, -11003, 1])
        self.assertEqual(before, state.snapshot())
        state._snapshot['wallet']['crystal'] = 6
        state.set_layouts(9, None, 1, [0, 0, 0, 0, 0, 0, -11003, 1])
        self.assertEqual(0, state.snapshot()['wallet']['crystal'])
        self.assertEqual(1000000, state.snapshot()['wallet']['credits'])
        self.assertEqual([0, 0, 0, 11003], state.snapshot()['vehicles'][0]['eqs'])

    def test_every_directive_can_be_bought_mounted_and_resupplied(self):
        # Exact 0.9.22 catalogue names/prices with a shared booster descriptor;
        # exercise depot purchase/install, layout fill, and CMD 308's direct
        # purchase using a UI slot-zero fixture for the slot-mismatch class;
        # the report did not capture the actual slot index.
        directives = {
            'aimingStabilizerBattleBooster': 10, 'camouflageBattleBooster': 12,
            'coatedOpticsBattleBooster': 8, 'enhancedAimDrivesBattleBooster': 10,
            'fireFightingBattleBooster': 2, 'improvedVentilationBattleBooster': 12,
            'lastEffortBattleBooster': 4, 'pedantBattleBooster': 6,
            'rammerBattleBooster': 12, 'rancorousBattleBooster': 2,
            'sixthSenseBattleBooster': 6, 'smoothDrivingBattleBooster': 10,
            'smoothTurretBattleBooster': 10, 'toolboxBattleBooster': 6,
            'virtuosoBattleBooster': 8}
        for name, price in sorted(directives.items()):
            for entrance in ('depot', 'layout', 'buy_and_equip'):
                with self.subTest(name=name, entrance=entrance):
                    requests, commands, garage = _request_modules()
                    vehicles, tankmen = _modules()
                    snapshot = copy.deepcopy(SNAPSHOT)
                    snapshot['wallet'] = dict(credits=1000000, gold=100,
                                              freeXP=0, crystal=price * 3)
                    snapshot['shopItemPrices'][11003] = price_catalogue.money(
                        price_catalogue.ARTEFACT_PRICES[name])
                    self.assertEqual({'crystal': price}, snapshot['shopItemPrices'][11003])
                    state = garage.GarageState(snapshot, vehicles_module=vehicles,
                                               tankmen_module=tankmen)
                    context = {'garage': state}
                    if entrance == 'depot':
                        bought = requests._buy_item(context, (0, 11003, 2, 0))
                        self.assertEqual(commands.RES_SUCCESS, bought.result_id)
                        self.assertEqual(2, state.snapshot()['inventoryItems'][11][11003])
                    if entrance == 'buy_and_equip':
                        mounted = requests.dispatch(
                            commands.CMD_BUY_AND_EQUIP_ITEM, context,
                            ([0, 11003, 9, 0, 0, 0],))
                    else:
                        layout = [0, 9, 0, 1, 8, 0, 0, 0, 0, 0, 0, 11003, 1]
                        mounted = requests._set_and_fill_layouts(context, (layout,))
                    self.assertEqual(commands.RES_SUCCESS, mounted.result_id)
                    self.assertEqual([0, 0, 0, 11003], state.snapshot()['vehicles'][0]['eqs'])
                    state.settle_battle_consumables(50001, [11003])
                    state.change_vehicle_setting(9, 16, 1)
                    store = _load('garage_store')
                    costs = store._settle_automatically(
                        state, 9, (1, 2, 4, 16), garage.GarageError)
                    self.assertEqual(0 if entrance == 'depot' else price,
                                     costs['equipment_crystal'])
                    self.assertEqual(price, state.snapshot()['wallet']['crystal'])
                    self.assertEqual([0, 0, 0, 11003], state.snapshot()['vehicles'][0]['eqs'])
                    self.assertEqual(1000000, state.snapshot()['wallet']['credits'])
                    self.assertEqual(100, state.snapshot()['wallet']['gold'])

    def test_ventilation_insufficient_bonds_refuses_without_changing_inventory(self):
        requests, commands, garage = _request_modules()
        vehicles, tankmen = _modules()
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = dict(credits=1000000, gold=100, freeXP=0, crystal=11)
        snapshot['shopItemPrices'][11003] = price_catalogue.money(
            price_catalogue.ARTEFACT_PRICES['improvedVentilationBattleBooster'])
        state = garage.GarageState(snapshot, vehicles_module=vehicles,
                                   tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())
        refused = requests._buy_item({'garage': state}, (0, 11003, 1, 0))
        self.assertEqual(commands.RES_FAILURE, refused.result_id)
        self.assertIn('11 crystal and needs 12', refused.error)
        self.assertEqual(before, state.snapshot())

    def test_directive_edit_and_resupply_do_not_buy_regular_consumables(self):
        garage, state, unused_vehicles, unused_tankmen = self._garage()
        record = state._snapshot['vehicles'][0]
        record['eqsLayout'] = [11001, 0, 0]
        state.set_layouts(9, None, 1, [11001, 1, 0, 0, 0, 0, 11003, 1])
        self.assertEqual([0, 0, 0, 11003], record['eqs'])
        self.assertEqual([11001, 0, 0, 11003], record['eqsLayout'])
        state.settle_battle_consumables(50001, [11003])
        record['settings'] = 16
        store_module = _load('garage_store')
        costs = store_module._settle_automatically(state, 9, (1, 2, 4, 16),
                                                  garage.GarageError)
        self.assertEqual(6, costs['equipment_crystal'])
        self.assertEqual([0, 0, 0, 11003], record['eqs'])
        self.assertEqual(88, state.snapshot()['wallet']['crystal'])

    def test_directive_cannot_occupy_regular_slots_or_be_resold(self):
        garage, state, unused_vehicles, unused_tankmen = self._garage()
        before = state.snapshot()
        with self.assertRaises(garage.GarageError):
            state.equip_equipments(9, [11003, 0, 0])
        self.assertEqual(before, state.snapshot())
        state.buy_item(11003)
        before = state.snapshot()
        with self.assertRaises(garage.GarageError):
            state.sell_item(11003)
        self.assertEqual(before, state.snapshot())

    def test_deluxe_removal_debits_bonds_and_resale_returns_only_credits(self):
        unused_garage, state, unused_vehicles, unused_tankmen = self._garage(300)
        state.equip_optional_device(9, 9002, 0)
        state.equip_optional_device(9, 0, 0, paid_removal=True)
        self.assertEqual(100, state.snapshot()['wallet']['crystal'])
        state.sell_item(9002)
        self.assertEqual(100, state.snapshot()['wallet']['crystal'])
        self.assertEqual(1500000, state.snapshot()['wallet']['credits'])

    def test_bond_award_and_consumed_directive_survive_restart_once(self):
        unused_garage, state, vehicles, tankmen = self._garage(100)
        state.equip_equipments(9, [0, 0, 0, 11003])
        snapshot = state.snapshot()
        snapshot['earningsPercent'] = 300
        store_module = _load('garage_store')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'garage_state.json')
            store = store_module.GarageStore(path)
            kwargs = dict(tankmen_module=tankmen, vehicles_module=vehicles,
                          rewards={'credits': 10, 'xp': 10, 'free_xp': 1,
                                   'crystal': 3}, equipment_used=[11003])
            first = store.apply_battle_crew_xp(snapshot, 'bonds:1', 50001, 10, 0, **kwargs)
            self.assertTrue(first['applied'])
            self.assertEqual(103, snapshot['wallet']['crystal'])
            self.assertEqual(9, first['awarded']['crystal'])
            self.assertEqual([0, 0, 0, 0], snapshot['vehicles'][0]['eqs'])
            restarted = store_module.GarageStore(path)
            snapshot['earningsPercent'] = 10000
            second = restarted.apply_battle_crew_xp(snapshot, 'bonds:1', 50001, 10, 0, **kwargs)
            self.assertFalse(second['applied'])
            self.assertEqual(103, snapshot['wallet']['crystal'])
            self.assertEqual(9, second['awarded']['crystal'])
            restored = copy.deepcopy(SNAPSHOT)
            restarted.apply(restored)
            self.assertEqual(103, restored['wallet']['crystal'])
            self.assertEqual([0, 0, 0, 0], restored['vehicles'][0]['eqs'])

    def test_results_include_the_medal_breakdown_and_directive_service_cost(self):
        receipt = _receipt()
        receipt['rewards']['crystal'] = 5
        receipt['crystal_rewards'] = {'warrior': 3, 'mainGun': 2}
        receipt['service_costs'] = {'equipment_crystal': 6}
        vehicle = _packed_vehicle(receipt)
        self.assertEqual(5, vehicle['crystal'])
        self.assertEqual(0, vehicle['originalCrystal'])
        self.assertEqual([('mainGun', 2), ('warrior', 3)], vehicle['eventCrystalList'])
        self.assertEqual((0, 0, 6), vehicle['autoEquipCost'])
        self.assertIn(b'eventCrystalList_warrior', vehicle['crystalReplay'])

    def test_scaled_bonds_results_and_medal_rows_equal_the_bank(self):
        for percent, total in ((1, 0), (50, 2), (100, 5), (250, 12),
                               (300, 15), (10000, 500)):
            with self.subTest(percent=percent):
                receipt = _receipt()
                receipt['rewards']['crystal'] = 5
                receipt['crystal_rewards'] = {'warrior': 3, 'mainGun': 2}
                receipt['awarded'] = economy.scale_rewards(
                    receipt['rewards'], 150, 150, bonds_percent=percent)
                receipt['service_costs'] = {'equipment_crystal': 12}
                vehicle = _packed_vehicle(receipt)
                self.assertEqual(total, vehicle['crystal'])
                self.assertGreaterEqual(vehicle['originalCrystal'], 0)
                self.assertEqual(total, vehicle['originalCrystal'] + sum(
                    amount for name, amount in vehicle['eventCrystalList']))
                self.assertEqual((0, 0, 12), vehicle['autoEquipCost'])
                self.assertEqual(5, receipt['rewards']['crystal'])
                self.assertEqual({'warrior': 3, 'mainGun': 2}, receipt['crystal_rewards'])

    def test_bonds_lifetime_progress_survives_result_retry_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'postbattle.json')
            store = postbattle_store.PostBattleStore(path)
            receipt = _receipt(store.account_key)
            receipt['rewards']['crystal'] = 5
            receipt['crystal_rewards'] = {'warrior': 3, 'mainGun': 2}
            store.set_progress_applier(lambda value: {'awarded':
                economy.scale_rewards(value['rewards'], 300, 300, 300)})
            self.assertTrue(store.accept(receipt))
            self.assertEqual(15, store.progress()['crystal'])
            restarted = postbattle_store.PostBattleStore(path)
            self.assertFalse(restarted.accept(receipt))
            self.assertEqual(15, restarted.progress()['crystal'])

    def test_finished_server_round_uses_frozen_directive_and_medal_tier(self):
        state = server.BattleState(map_name='01_karelia')
        state.client_build = server.CLIENT_BUILD_0922
        state.phase, state.round_id = 'battle', 7
        player = server.Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                               vehicle='ussr:R11_MS-1', team=1, account_key='a' * 32)
        player.effective_params = effective_params()
        player.effective_params['battle_booster'] = {'compact_descr': 11003,
                                                    'skill_overrides': {}}
        state.players[1] = player
        state.vehicle_catalogs[1] = [{'name': player.vehicle, 'level': 10}]
        state._freeze_round_participants([player])
        player.effective_params.pop('battle_booster')
        with mock.patch.object(server, 'award_battle_achievements',
                               return_value={('player', 1): ['warrior', 'mainGun']}):
            self.assertTrue(state._finish_battle(1, 'elimination'))
        receipt = list(state.result_receipts.values())[0]
        self.assertEqual(11003, receipt['battle_booster'])
        self.assertEqual(5, receipt['rewards']['crystal'])
        self.assertEqual(5, postbattle_store._receipt(receipt)['rewards']['crystal'])

    def test_booster_projection_is_bounded_and_does_not_mutate_input(self):
        snapshot = effective_params()
        snapshot['battle_booster'] = {'compact_descr': 11003,
                                    'skill_overrides': {'sixth_sense_delay': 2.0}}
        projected = wire.canonical(snapshot)
        self.assertIsNotNone(projected)
        projected['battle_booster']['skill_overrides']['sixth_sense_delay'] = 1.0
        self.assertEqual(2.0, snapshot['battle_booster']['skill_overrides']['sixth_sense_delay'])
        snapshot['battle_booster']['compact_descr'] = -1
        self.assertIsNone(wire.canonical(snapshot))

    def test_trained_designated_target_directive_extends_only_the_aim_sector(self):
        snapshot = _effective_params_snapshot(designated_target=True)
        # The fixture owns a completed, eligible gunner_rancorous perk.
        snapshot['battle_booster'] = {'compact_descr': 11003,
            'skill_overrides': {'designated_target_duration': 4.0,
                               'designated_target_sector': 0.0872664626}}
        source = {'x': 0.0, 'y': 0.0, 'z': 0.0, 'aim_yaw': 0.0}
        self.assertEqual(14.0, BotRuntime._designated_spot_duration(
            source, {'position': (0.0, 0.0, 100.0)}, snapshot))
        self.assertEqual(10.0, BotRuntime._designated_spot_duration(
            source, {'position': (100.0, 0.0, 0.0)}, snapshot))

    def test_direct_he_feedback_uses_damage_instead_of_penetration(self):
        for splash, damage, result in ((False, 150, 1), (False, 0, 0),
                                       (False, 0, 2), (True, 100, 1)):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            battle._avatar.playerVehicleID = 10
            battle._synchronise_player_identity(10)
            descriptor = _Descriptor()
            descriptor.gun.shots[0].shell.kind = 'HIGH_EXPLOSIVE'
            runtime.bigworld.entities[10] = types.SimpleNamespace(
                typeDescriptor=descriptor)
            target = {'engine_id': 11, 'local': False, 'kind': 'bot',
                      'network_id': 2, 'state': {'team': 2}}
            attacker = {'engine_id': 10, 'local': True, 'kind': 'player',
                        'network_id': 1, 'state': {'team': 1}}
            event = {'kind': 'bot_hit', 'damage': damage, 'shot_result': result,
                     'source': 'shot', 'splash': splash, 'attack_reason': 0}
            battle._present_combat_feedback(event, target, attacker)
            flags = battle._avatar.shot_results[0][0] >> 32
            constants = runtime.constants.VEHICLE_HIT_FLAGS
            expected = (constants.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION
                        if splash else (constants.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE
                        if damage else constants.MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE))
            self.assertTrue(flags & expected)
            self.assertEqual(result, event['shot_result'])
