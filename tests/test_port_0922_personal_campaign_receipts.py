"""Campaign evaluation and rewards commit with the durable battle receipt."""

import copy
import json
import pickle
import sys
import types
import unittest
from unittest import mock

import test_port_0922_garage as garage_fixture
import test_port_0922_personal_campaign_battle as battle_fixture


class PersonalCampaignReceiptTests(unittest.TestCase):
    def setUp(self):
        condition_case = battle_fixture.PersonalCampaignBattleTests()
        condition_case.setUp()
        self.receipt = copy.deepcopy(condition_case.receipt)
        self.definition = copy.deepcopy(condition_case.definitions[270])
        for stage in ('main', 'add'):
            self.definition[stage]['children'].append(('id', {
                'value': 'regular_4_3_15_' + stage, 'children': []}))
        self.definition['main']['children'].append(('bonusDelayed',
            battle_fixture.node('<bonusDelayed><tankwoman/></bonusDelayed>')))
        self.definition['main']['children'].append(('bonus', battle_fixture.node(
            '<bonus><token><id>token:pt:final:s1:t4</id><count>1</count>'
            '<limit>5</limit></token></bonus>')))
        self.definition['add']['children'].append(('bonus', battle_fixture.node(
            '<bonus><credits>500000</credits><token><id>free_award_list</id>'
            '<count>1</count></token></bonus>')))
        self.fixture = garage_fixture.GaragePersistenceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.stores = self.fixture.store_module
        self.campaign = self.stores.personal_campaign
        self.vehicles, self.tankmen = garage_fixture._modules()
        original = self.vehicles.VehicleDescr

        def describe(compactDescr=None, typeName=None):
            return (original(compactDescr=compactDescr) if typeName is None
                    else condition_case.describe(typeName))
        self.vehicles.VehicleDescr = describe
        # Reuse the real evaluator/settlement graph even when another test
        # previously loaded independent module fixtures into this process.
        import gui.mods.offline_lan_0922 as package
        patches = (
            mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.personal_campaign': self.campaign}),
            mock.patch.object(package, 'personal_campaign_battle',
                              condition_case.policy, create=True),
            mock.patch.object(self.campaign, 'mission_definition',
                              side_effect=lambda qid, *args: self.definition
                              if qid == 270 else None),
            mock.patch.object(self.campaign, '_resource',
                              return_value=battle_fixture.node('<tiles/>')),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.snapshot = self.fresh_snapshot()
        self.snapshot['personalMissionSelections'] = {'regular': [270]}
        self.snapshot['personalMissionOrders'] = 21
        self.store = self.fixture._store()

    def fresh_snapshot(self):
        return self.fixture._matching_snapshot()

    def settle(self, receipt_id, store=None, snapshot=None):
        return (store or self.store).apply_battle_crew_xp(
            self.snapshot if snapshot is None else snapshot, receipt_id,
            50001, 100, 1, tankmen_module=self.tankmen,
            vehicles_module=self.vehicles,
            rewards={'credits': 100, 'xp': 100, 'free_xp': 5},
            campaign_receipt=self.receipt)

    def test_mt15_reward_receipt_replay_and_restart_are_exactly_once(self):
        first = self.settle('campaign:1')
        self.assertTrue(first['applied'])
        self.assertEqual([270], first['personal_missions']['completed'])
        self.assertEqual([], first['personal_missions']['pending'])
        self.assertEqual({'270': 2}, self.snapshot['personalMissionProgress'])
        self.assertEqual({'270': 2}, self.snapshot['personalMissionRewarded'])
        self.assertEqual(600100, self.snapshot['wallet']['credits'])
        self.assertEqual(1, self.snapshot['personalMissionOrders'])
        mission = first['personal_missions']['missions'][0]
        self.assertEqual((0, 2), (mission['before'], mission['after']))
        self.assertEqual([1, 2], mission['paid_stages'])
        self.assertTrue(mission['main_complete'])
        self.assertTrue(mission['add_complete'])
        self.assertTrue(mission['tankwoman_pending'])
        self.assertEqual(1, mission['orders_earned'])
        self.assertEqual('regular_4_3_15_main', mission['main_quest'])
        self.assertEqual('regular_4_3_15_add', mission['add_quest'])
        before = copy.deepcopy(self.snapshot)
        duplicate = self.settle('campaign:1')
        self.assertFalse(duplicate['applied'])
        self.assertEqual(before, self.snapshot)

        restarted = self.fresh_snapshot()
        restarted_store = self.fixture._store()
        self.assertTrue(restarted_store.apply(restarted))
        self.assertEqual({'270': 2}, restarted['personalMissionProgress'])
        self.assertEqual({'270': 2}, restarted['personalMissionRewarded'])
        self.assertEqual(600100, restarted['wallet']['credits'])
        self.assertEqual(1, restarted['personalMissionOrders'])
        replay = self.settle('campaign:1', restarted_store, restarted)
        self.assertFalse(replay['applied'])
        self.assertEqual(first['personal_missions'], replay['personal_missions'])
        self.assertEqual(600100, restarted['wallet']['credits'])
        with open(self.fixture.path) as saved:
            persisted = json.load(saved)
        self.assertEqual(1, len(persisted['battleCrewReceipts']))
        self.assertEqual({'270': 2},
                         persisted['ledger']['personalMissions']['rewarded'])

    def test_incomplete_battle_keeps_native_conditions_without_new_rewards(self):
        self.receipt['interactions'][0]['damage'] = 0
        result = self.settle('campaign:unfinished')['personal_missions']
        self.assertEqual([], result['completed'])
        mission = result['missions'][0]
        self.assertEqual((0, 0), (mission['before'], mission['after']))
        self.assertFalse(mission['main_complete'])
        self.assertFalse(mission['add_complete'])
        self.assertEqual([], mission['paid_stages'])
        self.assertEqual([], mission['rewards'])
        self.assertFalse(mission['tankwoman_pending'])

    def test_mt2_event_completion_rewards_survive_replay_and_restart(self):
        qid = 32
        self.definition = battle_fixture.definition(
            '<vehicleDamage><eventCount/><greaterOrEqual>6</greaterOrEqual>'
            '</vehicleDamage>',
            '<vehicleKills><greaterOrEqual>1</greaterOrEqual></vehicleKills>')
        for stage, reward in (('main', 50000), ('add', 25000)):
            self.definition[stage]['children'].extend([
                ('id', {'value': 'regular_1_3_2_' + stage, 'children': []}),
                ('bonus', battle_fixture.node('<bonus><credits>%d</credits>'
                                              '</bonus>' % reward))])
        self.snapshot['personalMissionSelections'] = {'regular': [qid]}
        for event in self.receipt['interactions']:
            event['damage_events'] = 2
        with mock.patch.object(self.campaign, 'mission_definition',
                               side_effect=lambda current, *args:
                               self.definition if current == qid else None):
            first = self.settle('campaign:mt2')
            self.assertEqual([qid], first['personal_missions']['completed'])
            self.assertEqual({'32': 2}, self.snapshot['personalMissionProgress'])
            self.assertEqual({'32': 2}, self.snapshot['personalMissionRewarded'])
            self.assertEqual(175100, self.snapshot['wallet']['credits'])
            duplicate = self.settle('campaign:mt2')
            self.assertFalse(duplicate['applied'])
            restarted = self.fresh_snapshot()
            restarted_store = self.fixture._store()
            self.assertTrue(restarted_store.apply(restarted))
            replay = self.settle('campaign:mt2', restarted_store, restarted)
            self.assertFalse(replay['applied'])
            self.assertEqual({'32': 2}, restarted['personalMissionProgress'])
            self.assertEqual(175100, restarted['wallet']['credits'])

    def test_unsupported_condition_never_becomes_a_completed_result_card(self):
        conditions = self.definition['main']['children'][0][1]
        conditions['children'][0][1]['children'].append(('unavailableEvent',
            {'value': '', 'children': []}))
        result = self.settle('campaign:unsupported')['personal_missions']
        self.assertIn('270', result['unsupported'])
        self.assertEqual([], result['completed'])
        mission = result['missions'][0]
        self.assertFalse(mission['main_complete'])
        self.assertFalse(mission['add_complete'])

    def test_failed_reward_reports_completion_without_claiming_delivery(self):
        bonus = self.campaign.child(self.definition['add'], 'bonus')
        bonus['children'].append(('unknownReward', {'value': '1', 'children': []}))
        first = self.settle('campaign:pending')['personal_missions']
        self.assertEqual([], first['completed'])
        self.assertTrue(first['pending'])
        mission = first['missions'][0]
        self.assertEqual((0, 2), (mission['before'], mission['after']))
        self.assertEqual([], mission['paid_stages'])
        self.assertEqual([], mission['rewards'])
        self.assertFalse(mission['tankwoman_pending'])
        self.assertEqual(0, mission['orders_earned'])
        restarted = self.fresh_snapshot()
        restarted_store = self.fixture._store()
        self.assertTrue(restarted_store.apply(restarted))
        replay = self.settle('campaign:pending', restarted_store, restarted)
        self.assertFalse(replay['applied'])
        self.assertEqual(first, replay['personal_missions'])

    def test_operation_vehicle_touch_is_remapped_after_restart(self):
        self.snapshot['defaultVehicleSettings'] = 14
        operation = battle_fixture.node(
            '<tiles><quests><tokenQuest><id>operation:4</id><enabled>true</enabled>'
            '<conditions><preBattle><account><token><id>token:pt:final:s1:t4</id>'
            '<greaterOrEqual>1</greaterOrEqual><consume>1</consume></token>'
            '</account></preBattle></conditions><bonus><slots>1</slots>'
            '<vehicle>ussr:reward</vehicle></bonus></tokenQuest></quests></tiles>')
        import gui.mods.offline_lan_0922 as package
        native_factory = types.ModuleType('gui.mods.offline_lan_0922.bootstrap')
        calls = []

        def build_record(snapshot, vehicles, tankmen, indices, settings, name):
            # The native record factory has separate descriptor tests. This
            # seam supplies a valid second record to exercise the real reward
            # transaction, touched IDs, disk marker and restart remapping.
            calls.append(name)
            record = copy.deepcopy(snapshot['vehicles'][0])
            record.update(id=10, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                          vehicleTypeName=name)
            record['tankmen'] = dict((tid + 100, compact) for tid, compact in
                                     record['tankmen'].items())
            record['crew'] = [tid + 100 for tid in record['crew']]
            snapshot['vehicles'].append(record)
            snapshot.setdefault('vehicleTypeCompactDescrs', set((50001,))).add(50002)
            snapshot['shopItemPrices'][50002] = {'credits': 0}
            return 50002

        native_factory._build_purchased_vehicle = build_record
        items = types.ModuleType('items')
        items.ITEM_TYPE_INDICES = {'vehicle': 1}
        with mock.patch.object(self.campaign, '_resource', return_value=operation), \
                mock.patch.object(package, 'bootstrap', native_factory, create=True), \
                mock.patch.dict(sys.modules, {'items': items}):
            first = self.settle('campaign:operation')
        self.assertEqual([], first['personal_missions']['pending'])
        self.assertEqual(['operation:4'], first['personal_missions']['operation_rewards'])
        self.assertIn(10, first['touched_vehicles'])
        self.assertEqual(['ussr:reward'], calls)
        with open(self.fixture.path) as saved:
            marker = json.load(saved)['battleCrewReceipts'][0]
        self.assertIn(50002, marker['touched_vehicle_types'])

        restarted = self.fresh_snapshot()
        reward = copy.deepcopy(restarted['vehicles'][0])
        reward.update(id=110, compDescr=b'veh:10', vehicleTypeCompactDescr=50002,
                      vehicleTypeName='ussr:reward')
        reward['tankmen'] = dict((tid + 200, compact) for tid, compact in
                                 reward['tankmen'].items())
        reward['crew'] = [tid + 200 for tid in reward['crew']]
        restarted['vehicles'][0]['id'] = 109
        restarted['vehicles'].append(reward)
        restarted.setdefault('vehicleTypeCompactDescrs', set((50001,))).add(50002)
        restarted['shopItemPrices'][50002] = {'credits': 0}
        restarted_store = self.fixture._store()
        self.assertTrue(restarted_store.apply(restarted))
        replay = self.settle('campaign:operation', restarted_store, restarted)
        self.assertFalse(replay['applied'])
        self.assertIn(110, replay['touched_vehicles'])
        self.assertNotIn(10, replay['touched_vehicles'])
        self.assertEqual(['ussr:reward'], calls)


class PersonalCampaignPostbattlePushTests(unittest.TestCase):
    def _research_server(self):
        import test_port_0922_account_rpc as account_fixture
        harness = account_fixture.AccountRpcTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        snapshot = account_fixture._full_garage_snapshot()
        vehicles = types.ModuleType('items.vehicles')
        vehicles.getVehicleType = lambda cd: types.SimpleNamespace(unlocksDescrs=())
        items = types.ModuleType('items')
        items.vehicles = vehicles
        patch = mock.patch.dict(sys.modules, {
            'items': items, 'items.vehicles': vehicles})
        patch.start()
        self.addCleanup(patch.stop)
        server = account_fixture.FakeServer(
            lambda: harness.player,
            lambda delay, callback: harness.pending.append((delay, callback)), {
                'selected_vehicle': snapshot,
                'postbattle_store': types.SimpleNamespace(progress=lambda: {}),
                'postbattle_added_unlocks': {50002, 3002},
                'postbattle_added_eliteVehicles': {50002},
            })
        return server, harness

    def test_reward_research_is_announced_once_across_queued_pushes(self):
        server, harness = self._research_server()
        # A bought/rewarded tank is new; all other garage research is already
        # known to the client. Queue both pushes before native callbacks run.
        self.assertTrue(server.publish_postbattle_progress())
        self.assertTrue(server.publish_postbattle_progress())
        self.assertEqual(set(), server._context['postbattle_added_unlocks'])
        self.assertEqual(set(), server._context['postbattle_added_eliteVehicles'])
        harness._run()
        harness._run()
        first, second = [pickle.loads(value) for value in harness.player.updates]
        self.assertEqual({50002, 3002}, first['stats']['unlocks'])
        self.assertEqual({50002}, first['stats']['eliteVehicles'])
        self.assertNotIn(50001, first['stats']['unlocks'])
        self.assertNotIn(50001, first['stats']['eliteVehicles'])
        self.assertNotIn('unlocks', second['stats'])
        self.assertNotIn('eliteVehicles', second['stats'])
        self.assertEqual(2, harness.player.dossier_resyncs)

    def test_rejected_reward_research_push_remains_pending_for_retry(self):
        server, harness = self._research_server()
        player, harness.player = harness.player, None
        self.assertFalse(server.publish_postbattle_progress())
        self.assertEqual([], harness.pending)
        self.assertEqual({50002, 3002},
                         server._context['postbattle_added_unlocks'])
        self.assertEqual({50002},
                         server._context['postbattle_added_eliteVehicles'])
        harness.player = player
        self.assertTrue(server.publish_postbattle_progress())
        harness._run()
        update = pickle.loads(player.updates[-1])
        self.assertEqual({50002, 3002}, update['stats']['unlocks'])
        self.assertEqual({50002}, update['stats']['eliteVehicles'])
        self.assertEqual(set(), server._context['postbattle_added_unlocks'])
        self.assertEqual(set(), server._context['postbattle_added_eliteVehicles'])

    def test_failed_reward_research_push_preserves_new_pending_additions(self):
        server, harness = self._research_server()
        self.assertTrue(server.publish_postbattle_progress())
        # Another reward can arrive while the first account update is queued.
        server._context['postbattle_added_unlocks'].add(3003)
        with mock.patch.object(harness.player, 'update',
                               side_effect=RuntimeError('native update failed')):
            harness._run()
        self.assertEqual([], harness.player.updates)
        self.assertEqual({50002, 3002, 3003},
                         server._context['postbattle_added_unlocks'])
        self.assertEqual({50002},
                         server._context['postbattle_added_eliteVehicles'])
        self.assertTrue(server.publish_postbattle_progress())
        harness._run()
        update = pickle.loads(harness.player.updates[-1])
        self.assertEqual({50002, 3002, 3003}, update['stats']['unlocks'])
        self.assertEqual({50002}, update['stats']['eliteVehicles'])
        self.assertEqual(set(), server._context['postbattle_added_unlocks'])
        self.assertEqual(set(), server._context['postbattle_added_eliteVehicles'])

    def test_account_replacement_restores_research_before_or_during_publication(self):
        for phase in ('queued', 'publishing'):
            with self.subTest(phase=phase):
                server, harness = self._research_server()
                previous_player = harness.player
                replacement = type(previous_player)()
                self.assertTrue(server.publish_postbattle_progress())
                retired_callback = harness.pending[0][1]
                if phase == 'queued':
                    harness.player = replacement
                    harness._run()
                else:
                    def replace_account(unused_payload):
                        harness.player = replacement
                    with mock.patch.object(previous_player, 'update',
                                           side_effect=replace_account):
                        harness._run()
                self.assertEqual([], replacement.updates)
                self.assertEqual(0, replacement.dossier_resyncs)
                self.assertEqual({50002, 3002},
                                 server._context['postbattle_added_unlocks'])
                self.assertEqual({50002},
                                 server._context['postbattle_added_eliteVehicles'])
                self.assertTrue(server.publish_postbattle_progress())
                harness._run()
                self.assertEqual(1, len(replacement.updates))
                update = pickle.loads(replacement.updates[0])
                self.assertEqual({50002, 3002}, update['stats']['unlocks'])
                self.assertEqual({50002}, update['stats']['eliteVehicles'])
                # A stale callback must not restore a notification that the
                # replacement Account has already consumed successfully.
                retired_callback()
                self.assertEqual(set(), server._context['postbattle_added_unlocks'])
                self.assertEqual(set(),
                                 server._context['postbattle_added_eliteVehicles'])

    def test_account_replacement_during_inventory_refresh_restores_research_once(self):
        server, harness = self._research_server()
        server._context['postbattle_touched_vehicles'] = {9}
        refreshed = mock.Mock()
        server._context['on_inventory_refreshed'] = refreshed
        callbacks = []

        def delay_refresh(unused_diff, after_refresh, after_failure, is_current):
            callbacks.append((after_refresh, after_failure, is_current))

        with mock.patch(
                'gui.mods.offline_lan_0922.account_rpc.server.'
                '_refresh_garage_views', side_effect=delay_refresh):
            self.assertTrue(server.publish_postbattle_progress())
            harness._run()
            self.assertTrue(server.inventory_refresh_pending)
            harness.player = type(harness.player)()
            retired_finish, retired_failure, is_current = callbacks[0]
            self.assertFalse(is_current())
            retired_finish()
            self.assertFalse(server.inventory_refresh_pending)
            refreshed.assert_not_called()
            self.assertEqual({9}, server._context['postbattle_touched_vehicles'])
            self.assertEqual({50002, 3002},
                             server._context['postbattle_added_unlocks'])
            self.assertEqual({50002},
                             server._context['postbattle_added_eliteVehicles'])

            self.assertTrue(server.publish_postbattle_progress())
            harness._run()
            callbacks[1][0]()
            self.assertFalse(server.inventory_refresh_pending)
            refreshed.assert_called_once_with()
            self.assertEqual(1, harness.player.dossier_resyncs)
            retired_finish()
            retired_failure('late native refresh failure')
            self.assertEqual(set(), server._context['postbattle_added_unlocks'])
            self.assertEqual(set(), server._context['postbattle_added_eliteVehicles'])
            self.assertEqual(set(), server._context['postbattle_touched_vehicles'])

    def test_reward_balances_publish_without_replaying_elite_notifications(self):
        import test_port_0922_account_rpc as account_fixture
        harness = account_fixture.AccountRpcTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        snapshot = account_fixture._full_garage_snapshot()
        snapshot.update(accountSlots=32, accountBerths=34,
                        personalMissionOrders=22, premiumExpiryTime=4104777660,
                        personalMissionTokens={'operation_complete': [4104777660, 1]})
        vehicles = types.ModuleType('items.vehicles')
        vehicles.getVehicleType = lambda cd: types.SimpleNamespace(unlocksDescrs=())
        items = types.ModuleType('items')
        items.vehicles = vehicles
        server = account_fixture.FakeServer(
            lambda: harness.player,
            lambda delay, callback: harness.pending.append((delay, callback)),
            {'selected_vehicle': snapshot,
             'postbattle_store': types.SimpleNamespace(progress=lambda: {})})
        with mock.patch.dict(sys.modules, {'items': items, 'items.vehicles': vehicles}):
            self.assertTrue(server.publish_postbattle_progress())
            harness._run()
        update = pickle.loads(harness.player.updates[-1])
        self.assertEqual(32, update['stats']['slots'])
        self.assertEqual(34, update['stats']['berths'])
        self.assertEqual(22, update['tokens']['free_award_list'][1])
        self.assertEqual(1, update['tokens']['operation_complete'][1])
        self.assertEqual(4104777660, update['account']['premiumExpiryTime'])
        self.assertIn('potapovQuests', update)
        self.assertEqual(5, update['potapovQuests']['regular']['slots'])
        self.assertNotIn('eliteVehicles', update['stats'])
        self.assertNotIn('unlocks', update['stats'])

if __name__ == '__main__':
    unittest.main()
