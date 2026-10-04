"""Mission event boundaries and server -> receipt -> settlement regression."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest

from test_port_0922_personal_campaign_battle import node, definition
from test_port_0922_postbattle import (
    BattleState, Player, _Socket, _latest_receipt, CLIENT_BUILD_0922,
    lan_server_module as server, lan_client_module as client, postbattle_store)
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy
from gui.mods.offline_lan_0922 import mission_events


class MissionEventConditionsTests(unittest.TestCase):
    def setUp(self):
        self.conditions = json.loads((Path(__file__).parent /
            'fixtures/personal_mission_event_conditions_0922.json').read_text())
        self.receipt = dict(player_id=1, vehicle='ussr:medium', team=1, winner=1,
                            death_reason=-1, battle_mode='regular',
                            stats={'damage': 4000}, public_results=[], interactions=[])
        for target in range(2, 6):
            self.receipt['public_results'].append(dict(actor_kind='bot', actor_id=target,
                team=2, vehicle='ussr:medium'))
            self.receipt['interactions'].append(dict(target_kind='bot', target_id=target,
                damage=1000, damage_events=1, target_kills=1, death_reason=0,
                mission_events_complete=True, mission_events=[
                    ['critical', 1000, 1 << 16], ['damage', 120000, 1000, True],
                    ['kill', 180000, 0, True, 100.0]]))

    def check(self, condition, stage='main'):
        return policy._condition('postBattle', node(condition[stage]),
                                  policy._Facts(self.receipt, None))

    def test_four_operations_mt3_mt4_ht2_main_and_honours(self):
        for condition in self.conditions:
            for stage in ('main', 'add'):
                with self.subTest(name=condition['name'], stage=stage):
                    self.assertEqual((True, set()), self.check(condition, stage))

    def test_each_reported_condition_rejects_the_near_miss(self):
        for condition in self.conditions:
            with self.subTest(name=condition['name']):
                before = copy.deepcopy(self.receipt)
                for interaction in self.receipt['interactions']:
                    events = interaction['mission_events']
                    if condition['chain'] == 2:
                        events[-1][4] = 101.0
                    elif condition['number'] == 4:
                        events[1][3] = False
                    elif condition['operation'] <= 2:
                        events[1][1] = 120001
                    else:
                        events[-1][1] = 180001
                self.assertEqual((False, set()), self.check(condition))
                self.receipt = before

    def test_mt4_requires_actual_track_breaks_and_honours_needs_tracked_kill(self):
        for condition in self.conditions:
            if condition['chain'] != 3 or condition['number'] != 4:
                continue
            before = copy.deepcopy(self.receipt)
            for interaction in self.receipt['interactions']:
                interaction['mission_events'][0][2] = 1 << 12
            self.assertEqual((False, set()), self.check(condition))
            self.receipt = copy.deepcopy(before)
            for interaction in self.receipt['interactions']:
                interaction['mission_events'][-1][3] = False
            self.assertEqual((True, set()), self.check(condition))
            self.assertEqual((False, set()), self.check(condition, 'add'))
            self.receipt = before

    def test_legacy_or_truncated_history_never_completes_from_totals(self):
        for mode in ('legacy', 'truncated'):
            for row in self.receipt['interactions']:
                if mode == 'legacy':
                    for key in mission_events.FIELDS:
                        row.pop(key, None)
                else:
                    row.update(mission_events=[], mission_events_complete=False)
            for condition in self.conditions:
                with self.subTest(mode=mode, name=condition['name']):
                    self.assertIsNone(self.check(condition)[0])

    def test_friendly_target_events_are_excluded(self):
        for row in self.receipt['public_results']:
            row['team'] = 1
        for condition in self.conditions:
            self.assertEqual((False, set()), self.check(condition))


class MissionEventReceiptTests(unittest.TestCase):
    def state(self, path=None):
        state = BattleState(map_name='01_karelia', receipt_state_path=path)
        state.client_build, state.phase = CLIENT_BUILD_0922, 'battle'
        state.tick = int((server.PREBATTLE_SECONDS + 60) * server.TICK_HZ)
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                       vehicle='ussr:R11_MS-1', team=1, account_key='a' * 32)
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='Bob',
                        vehicle='ussr:R11_MS-1', team=2, account_key='b' * 32)
        first.x, second.x = 0., 100.
        state.players = {1: first, 2: second}
        state._freeze_round_participants((first, second))
        return state, first, second

    def test_server_restart_client_validation_and_exactly_once_mission_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state, first, second = self.state(path)
            actor, target = ('player', 1), ('player', 2)
            tracked = {'destroyed': ['leftTrackHealth']}
            state._record_critical_damage(actor, target, {}, tracked)
            state._record_critical_damage(actor, target, tracked, tracked)
            # Repair then another admitted track break is a second event.
            state._record_critical_damage(actor, target, {}, tracked)
            second.alive, second.health, second.death_reason = False, 0, 0
            state._record_damage(actor, target, 240, tracked)
            state._record_frag('player', 1, 2, 'player', 2, distance=100.)
            state._increment_interaction(actor, target, 'stun_duration', 1.125)
            self.assertTrue(state._finish_battle(1, 'elimination'))
            receipt = _latest_receipt(state, first.account_key)
            history = receipt['interactions'][0]['mission_events']
            self.assertEqual([
                ['critical', 60000, 1 << 16], ['critical', 60000, 1 << 16],
                ['damage', 60000, 240, True, 100., True, None],
                ['kill', 60000, 0, True, 100., True, True]], history)
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = BattleState(map_name='01_karelia', receipt_state_path=path)
            replay = _latest_receipt(restarted, first.account_key)
            self.assertEqual(receipt, replay)
            self.assertEqual(1.125, replay['interactions'][0]['stun_duration'])
            selections = {'personalMissionSelections': {'regular': [33]},
                          'personalMissionProgress': {}}
            quest = definition('<vehicleDamage><limittedTime>120</limittedTime>'
                               '<greaterOrEqual>1</greaterOrEqual></vehicleDamage>',
                               '<isAlive/>', minimum=1)
            vehicles = types.SimpleNamespace(VehicleDescr=lambda **unused:
                types.SimpleNamespace(type=types.SimpleNamespace(tags={'mediumTank'}, level=10)))
            calls = []
            def settle(row):
                result = policy.evaluate(selections, row, vehicles, lambda unused: quest)
                selections['personalMissionProgress'].update(result['completed'])
                calls.append(result['completed'])
                return {}
            store = postbattle_store.PostBattleStore(path=None)
            store._account_key = first.account_key
            store.set_progress_applier(settle)
            self.assertTrue(store.accept(replay))
            self.assertFalse(store.accept(replay))
            self.assertEqual([{'33': 2}], calls)
            # Old receipts retain missing evidence, including optional SPG
            # fields from before these counters were introduced.
            legacy = copy.deepcopy(receipt)
            for interaction in legacy['interactions']:
                for key in tuple(mission_events.FIELDS) + (
                        'damage_events', 'kills_assisted_stun', 'kills_assisted_track'):
                    interaction.pop(key, None)
            self.assertTrue(client._valid_battle_receipt(legacy))
            self.assertEqual(legacy, server._persisted_result_receipt(legacy))

    def test_pre_hit_immobilization_and_enemy_only_events(self):
        state, first, second = self.state()
        actor, target = ('player', 1), ('player', 2)
        tracked = {'destroyed': ['leftTrackHealth']}
        second.alive, second.health, second.critical = False, 0, tracked
        state._record_damage(actor, target, 100, {})
        state._record_critical_damage(actor, target, {}, tracked)
        state._record_frag('player', 1, 2, 'player', 2, distance=10.)
        events = state._receipt_interactions(actor)[0]['mission_events']
        self.assertFalse(events[0][3])
        self.assertFalse(events[-1][3])
        first.team = 2
        before = copy.deepcopy(events)
        state._record_damage(actor, target, 100, tracked)
        state._record_critical_damage(actor, target, {}, tracked)
        self.assertEqual(before, state._receipt_interactions(actor)[0]['mission_events'])

    def test_history_cap_is_bounded_and_explicitly_incomplete(self):
        state, first, second = self.state()
        for unused in range(mission_events.MAX_EVENTS + 1):
            state._record_damage(('player', 1), ('player', 2), 1, {})
        row = state._receipt_interactions(('player', 1))[0]
        self.assertEqual(mission_events.MAX_EVENTS, len(row['mission_events']))
        self.assertFalse(row['mission_events_complete'])
        self.assertTrue(mission_events.valid(row))
        self.assertTrue(state._finish_battle(1, 'elimination'))
        receipt = _latest_receipt(state, first.account_key)
        self.assertLess(len(json.dumps(receipt).encode('utf8')), server.MAX_LINE_BYTES)
        self.assertTrue(client._valid_battle_receipt(receipt))

    def test_malformed_histories_fail_both_receipt_readers(self):
        state, first, second = self.state()
        state._record_damage(('player', 1), ('player', 2), 1, {})
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, first.account_key)
        for events in ([['kill', 1, 0, True, float('nan')]],
                       [['damage', True, 1, False]], [['damage', 1, 0, False]],
                       [['critical', 2, 1], ['critical', 1, 1]],
                       [['unsupported', 1, 1]]):
            with self.subTest(events=events):
                value = copy.deepcopy(receipt)
                value['interactions'][0]['mission_events'] = events
                self.assertFalse(client._valid_battle_receipt(value))
                with self.assertRaises(ValueError):
                    server._persisted_result_receipt(value)
                with self.assertRaises(ValueError):
                    postbattle_store._receipt(value)


class HT4ConditionsTests(MissionEventReceiptTests):
    def test_internal_critical_mask_counts_devices_and_crew_not_external_parts(self):
        for index in range(8):
            expected = int(index in (0, 1, 2, 3, 6))
            for mask in (1 << index, 1 << (index + 12),
                         (1 << index) | (1 << (index + 12))):
                self.assertEqual(expected, mission_events.internal_critical_count(mask))
        self.assertEqual(5, mission_events.internal_critical_count(31 << 24))
        self.assertEqual(10, mission_events.internal_critical_count(
            mission_events.INTERNAL_CRITICAL_MASK))

    def test_all_four_ht4_conditions_at_threshold_and_one_below(self):
        rows = json.loads((Path(__file__).parent /
            'fixtures/ht4_conditions_0922.json').read_text())
        for row, threshold in zip(rows, (1, 3, 5, 6)):
            with self.subTest(name=row['name']):
                state, player, victim = self.state()
                yellow = {'devices': [{'name': 'engineHealth', 'state': 'critical'}]}
                for unused in range(threshold - 1):
                    state._record_critical_damage(('player', 1), ('player', 2), {}, yellow)
                # Each newly damaged device after repair is a fresh transition.
                before = state._receipt_interactions(('player', 1))
                state._record_critical_damage(('player', 1), ('player', 2), {}, yellow)
                # Re-publication of the unchanged yellow state is not a new hit.
                state._record_critical_damage(('player', 1), ('player', 2), yellow, yellow)
                for pid in range(3, 7):
                    state.players[pid] = Player(pid, _Socket(), ('127.0.0.1', pid),
                        name='Enemy%d' % pid, vehicle='ussr:R11_MS-1', team=2,
                        account_key=str(pid) * 32)
                state._freeze_round_participants(tuple(state.players.values()))
                for pid in range(2, 7):
                    state.players[pid].alive = False
                    state.players[pid].health = 0
                    state.players[pid].death_reason = 0
                    state._record_frag('player', 1, 2, 'player', pid)
                state._finish_battle(1, 'elimination')
                receipt = _latest_receipt(state, player.account_key)
                self.assertTrue(client._valid_battle_receipt(receipt))
                restored = postbattle_store._receipt(server._persisted_result_receipt(receipt))
                facts = policy._Facts(restored, None)
                self.assertEqual(threshold, facts.result('innerModuleCritCount'))
                self.assertEqual(0, facts.result('innerModuleDestrCount'))
                for part in ('main', 'add'):
                    self.assertEqual((True, set()), policy._condition(
                        'postBattle', node(row[part]), facts))
                quest = definition('', tags='heavyTank', minimum=1)
                for part in ('main', 'add'):
                    quest[part] = node('<quest><conditions>' + row[part] +
                                       '</conditions></quest>')
                selection = {'personalMissionSelections': {'regular': [row['qid']]},
                             'personalMissionProgress': {}}
                vehicles = types.SimpleNamespace(VehicleDescr=lambda **unused:
                    types.SimpleNamespace(type=types.SimpleNamespace(
                        tags={'heavyTank'}, level=10)))
                completed = policy.evaluate(selection, restored, vehicles,
                                            lambda unused: quest)
                self.assertEqual({str(row['qid']): 2}, completed['completed'])
                self.assertEqual({}, completed['unsupported'])
                if row['operation'] == 1:
                    restored['winner'] = 2
                else:
                    for item in restored['interactions']:
                        item['target_kills'] = 0
                self.assertEqual((True, set()), policy._condition(
                    'postBattle', node(row['main']), policy._Facts(restored, None)))
                self.assertEqual((False, set()), policy._condition(
                    'postBattle', node(row['add']), policy._Facts(restored, None)))
                restored['interactions'] = before
                self.assertEqual((False, set()), policy._condition(
                    'postBattle', node(row['main']), policy._Facts(restored, None)))

    def test_ht4_external_friendly_and_missing_evidence_do_not_award(self):
        state, player, victim = self.state()
        state._record_critical_damage(('player', 1), ('player', 2), {},
            {'destroyed': ['leftTrackHealth', 'gunHealth', 'surveyingDeviceHealth']})
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, player.account_key)
        self.assertEqual(0, policy._Facts(receipt, None).result('innerModuleCritCount'))
        receipt['interactions'][0]['mission_events'] = [['critical', 1000, 1]]
        self.assertEqual(1, policy._Facts(receipt, None).result('innerModuleCritCount'))
        for row in receipt['public_results']:
            row['team'] = 1
        self.assertEqual(0, policy._Facts(receipt, None).result('innerModuleCritCount'))
        for row in receipt['public_results']:
            row['team'] = 2
        receipt['interactions'][0]['mission_events_complete'] = False
        self.assertIsNone(policy._Facts(receipt, None).result('innerModuleCritCount'))
        receipt.pop('interactions')
        self.assertIsNone(policy._Facts(receipt, None).result('innerModuleCritCount'))


class TD2LT5ConditionsTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((Path(__file__).parent /
            'fixtures/td2_lt5_conditions_0922.json').read_text())
        self.receipt = {
            'team': 1, 'death_reason': -1,
            'stats': {'damage': 5000, 'internal_crits_at_end': 0},
            'public_results': [dict(actor_kind='bot', actor_id=i, team=2)
                               for i in range(2, 8)],
            'interactions': [dict(target_kind='bot', target_id=i,
                mission_events=[['critical', 1000, (1 << 12) | (1 << 24)]],
                mission_events_complete=True, kills_assisted_radio=1,
                assist_radio=100) for i in range(2, 8)]}

    def check(self, row, part='main'):
        return policy._condition('postBattle', node(row[part]),
                                 policy._Facts(self.receipt, None))[0]

    def test_all_four_campaigns_main_and_honours(self):
        for row in self.rows:
            with self.subTest(name=row['name']):
                self.assertTrue(self.check(row))
                self.assertTrue(self.check(row, 'add'))
                if row['chain'] == 4:
                    self.receipt['stats']['internal_crits_at_end'] = 1
                    self.assertTrue(self.check(row))
                    self.assertFalse(self.check(row, 'add'))
                    self.receipt['stats']['internal_crits_at_end'] = 0

    def test_external_or_yellow_modules_do_not_complete_td2(self):
        for mask in (1 << 16, 1 << 17, 1 << 19, 1 << 0, 1 << 6):
            for item in self.receipt['interactions']:
                item['mission_events'][0][2] = mask
            for row in self.rows:
                if row['chain'] == 4:
                    self.assertFalse(self.check(row))

    def test_lt5_requires_lethal_spot_assist_not_previous_damage_or_spot(self):
        for item in self.receipt['interactions']:
            item.update(kills_assisted_radio=0, spotted=1, target_kills=1)
        for row in self.rows:
            if row['chain'] == 1:
                self.assertFalse(self.check(row))
        for item in self.receipt['interactions']:
            item.pop('kills_assisted_radio')
        self.assertIsNone(self.check(self.rows[0]))

    def test_friendly_and_incomplete_critical_history_cannot_award_td2(self):
        td = next(row for row in self.rows if row['chain'] == 4)
        for item in self.receipt['interactions']:
            item['mission_events_complete'] = False
        self.assertIsNone(self.check(td))
        for item in self.receipt['public_results']:
            item['team'] = 1
        self.assertFalse(self.check(td))


class SpotKillReceiptTests(MissionEventReceiptTests):
    def test_historical_receipt_does_not_invent_a_clean_end_state(self):
        state, spotter, unused = self.state()
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, spotter.account_key)
        receipt['stats'].pop('internal_crits_at_end')
        for row in receipt['public_results']:
            row['stats'].pop('internal_crits_at_end', None)
        for restored in (server._persisted_result_receipt(receipt),
                         postbattle_store._receipt(receipt)):
            self.assertNotIn('internal_crits_at_end', restored['stats'])
            self.assertIsNone(policy._Facts(restored, None).result(
                'isAnyOurCrittedInnerModules'))

    def test_lethal_radio_credit_survives_receipt_and_is_idempotent(self):
        for target_kind in ('player', 'bot'):
            with self.subTest(target_kind=target_kind):
                state, spotter, target_player = self.state()
                shooter = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
                state.players[3] = shooter
                target = (target_kind, 2)
                if target_kind == 'bot':
                    state.bot_manifest = [{'id': 2, 'team': 2, 'name': 'Bot',
                        'vehicle': 'ussr:R11_MS-1'}]
                    state.bot_states[2] = {'id': 2, 'team': 2, 'alive': True,
                        'health': 100, 'vehicle': 'ussr:R11_MS-1',
                        'x': 100., 'y': 0., 'z': 0.}
                state.player_spotted[1] = {target}
                state._record_damage(('player', 3), target, 20, {})
                interaction = state._statistics_interaction(('player', 1), target)
                self.assertEqual(0, interaction['kills_assisted_radio'])
                if target_kind == 'player':
                    target_player.alive, target_player.health = False, 0
                else:
                    state.bot_states[2].update(alive=False, health=0)
                state._record_damage(('player', 3), target, 1, {})
                state._record_kill(('player', 3), target, 0)
                self.assertEqual(1, interaction['kills_assisted_radio'])
                state._record_kill(('player', 3), target, 0)
                self.assertEqual(1, interaction['kills_assisted_radio'])
                state._finish_battle(1, 'elimination')
                receipt = _latest_receipt(state, spotter.account_key)
                self.assertTrue(client._valid_battle_receipt(receipt))
                self.assertEqual(1, postbattle_store._receipt(receipt)[
                    'interactions'][0]['kills_assisted_radio'])

    def test_shooter_own_spot_suppresses_radio_kill_credit(self):
        state, spotter, target_player = self.state()
        state.players[3] = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        target = ('player', 2)
        state.player_spotted.update({1: {target}, 3: {target}})
        target_player.alive, target_player.health = False, 0
        state._record_damage(('player', 3), target, 100, {})
        state._record_kill(('player', 3), target, 0)
        self.assertEqual([], state._receipt_interactions(('player', 1)))

    def test_canonical_zero_hp_loss_kill_credits_current_spotters_only(self):
        state, spotter, victim = self.state()
        state.players[3] = Player(3, _Socket(), ('127.0.0.1', 3), team=1)
        target = ('player', 2)
        # A crew knockout leaves positive hull HP, yet is a canonical kill.
        victim.alive, victim.health = False, 100
        state.player_spotted[1] = {target}
        self.assertTrue(state._record_frag('player', 3, 2, 'player', 2))
        row = state._statistics_interaction(('player', 1), target)
        self.assertEqual(1, row['kills_assisted_radio'])
        self.assertEqual(0, row['assist_radio'])
        # Earlier damage does not earn the kill after this observer loses
        # contact. The friendly kill path must not award it either.
        for team in (1, 2):
            state, spotter, victim = self.state()
            state.players[3] = Player(3, _Socket(), ('127.0.0.1', 3), team=team)
            state.player_spotted[1] = {target}
            state._record_damage(('player', 3), target, 20, {})
            if team == 1:
                state.player_spotted[1].clear()
            victim.alive = False
            state._record_frag('player', 3, 2, 'player', 2)
            self.assertEqual(0, state._statistics_interaction(
                ('player', 1), target)['kills_assisted_radio'])

    def test_td2_secondary_reads_repaired_final_internal_state(self):
        state, player, enemy = self.state()
        critical = {'destroyed': ['engineHealth'], 'crew_ko': ['driver']}
        state._record_critical_damage(('player', 2), ('player', 1), {}, critical)
        for current, expected in ((critical, 1), ({'destroyed': ['leftTrackHealth']}, 0),
                                  ({'devices': [], 'destroyed': [], 'crew_ko': []}, 0)):
            player.critical = current
            state._finalize_vehicle_statistics()
            self.assertEqual(expected, state._receipt_statistics(
                state._statistics_row('player', 1))['internal_crits_at_end'])
