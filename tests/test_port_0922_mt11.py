"""Ramming missions use one admitted collision, not end-of-battle guesses.

Conditions are from the available 0.9.22 RU #788 reference XML. Production
continues to read the installed CN #1513 definitions; no quest is overwritten.
"""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest

from test_port_0922_personal_campaign_battle import node, definition
import test_port_0922_mission_events as event_fixture
import test_port_0922_server_projectiles as projectile_fixture
from test_port_0922_postbattle import _latest_receipt, postbattle_store
from test_port_0922_postbattle import lan_client_module as client
from test_port_0922_postbattle import lan_server_module as server
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy
from gui.mods.offline_lan_0922 import mission_events


CONDITIONS = json.loads((Path(__file__).parent /
                        'fixtures/mt11_conditions_0922.json').read_text())


def vehicles(level=8, target_level=9):
    return types.SimpleNamespace(VehicleDescr=lambda typeName:
        types.SimpleNamespace(type=types.SimpleNamespace(
            tags={'mediumTank'}, level=(target_level if typeName == 'enemy'
                                      else level))))


class MT11ConditionsTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(player_id=1, vehicle='medium', team=1, winner=1,
            death_reason=-1, battle_mode='regular', stats={'damage': 100},
            public_results=[dict(actor_kind='bot', actor_id=2, team=2,
                                 vehicle='enemy')],
            interactions=[dict(target_kind='bot', target_id=2,
                damage=100, damage_events=1, target_kills=1, death_reason=2,
                mission_events_version=2, mission_events_complete=True,
                mission_events=[['damage', 1000, 100, False],
                                ['ram', 1000, 100, 25, True, True, False],
                                ['kill', 1000, 2, False, None]])])

    def check(self, operation, stage='main', descriptions=None):
        return policy._condition('postBattle', node(CONDITIONS[operation-1][stage]),
            policy._Facts(self.receipt, descriptions or vehicles()))

    def test_all_four_operations_complete_main_and_honours(self):
        for op in range(1, 5):
            for stage in ('main', 'add'):
                with self.subTest(operation=op, stage=stage):
                    self.assertEqual((True, set()), self.check(op, stage))

    def test_first_operation_requires_ram_damage_but_not_a_kill(self):
        row = self.receipt['interactions'][0]
        row.update(target_kills=0, death_reason=-1)
        row['mission_events'] = row['mission_events'][:2]
        row['mission_events'][1][4] = False
        self.assertEqual((True, set()), self.check(1))
        self.assertEqual((False, set()), self.check(2))
        # A subsequent shell kill must not erase the earlier ram damage.
        row.update(target_kills=1, death_reason=0)
        row['mission_events'].append(['kill', 2000, 0, False, None])
        self.assertEqual((True, set()), self.check(1))
        self.assertEqual((False, set()), self.check(2))

    def test_shell_damage_and_friendly_rams_do_not_complete(self):
        self.receipt['interactions'][0]['mission_events'].pop(1)
        self.receipt['interactions'][0]['death_reason'] = 0
        self.assertEqual((False, set()), self.check(1))
        self.assertEqual((False, set()), self.check(2))
        self.setUp()
        self.receipt['public_results'][0]['team'] = 1
        for op in range(1, 5):
            self.assertEqual((False, set()), self.check(op))

    def test_survival_belongs_to_the_killing_ram_not_final_battle_state(self):
        self.receipt['death_reason'] = 0
        self.assertEqual((True, set()), self.check(3))
        self.assertEqual((False, set()), self.check(3, 'add'))
        self.receipt['death_reason'] = -1
        self.receipt['interactions'][0]['mission_events'][1][5] = False
        self.assertEqual((False, set()), self.check(3))
        # A different earlier contact survived does not qualify the fatal ram.
        self.receipt['interactions'][0]['mission_events'].insert(
            0, ['ram', 500, 10, 0, False, True, False])
        self.assertEqual((False, set()), self.check(3))

    def test_level_difference_uses_attacker_and_target_descriptors(self):
        self.assertEqual((False, set()), self.check(4, descriptions=vehicles(8, 8)))
        self.assertEqual((True, set()), self.check(4, descriptions=vehicles(8, 9)))
        self.assertEqual((True, set()), self.check(4, descriptions=vehicles(8, 10)))
        self.assertEqual((False, set()), self.check(4, descriptions=vehicles(9, 8)))
        broken = types.SimpleNamespace(VehicleDescr=lambda **kw: None)
        self.assertIsNone(self.check(4, descriptions=broken)[0])

    def test_legacy_and_incomplete_ram_evidence_stays_unknown(self):
        row = self.receipt['interactions'][0]
        row['mission_events_complete'] = False
        for op in (1, 3):
            self.assertIsNone(self.check(op)[0])
        row['mission_events_complete'] = True
        row.pop('mission_events_version')
        row['mission_events'].pop(1)
        for op in (1, 3):
            self.assertIsNone(self.check(op)[0])
        # The existing kill reason is sufficient for the unmodified second tier.
        self.assertEqual((True, set()), self.check(2))

    def test_shared_ramming_and_level_modifiers_keep_their_full_conditions(self):
        facts = policy._Facts(self.receipt, vehicles())
        condition = node('<vehicleKills><rammingInfo>stayedAlive dealtMoreDamage'
            '</rammingInfo><attackReason>2</attackReason><lvlDiff>1</lvlDiff>'
            '<greaterOrEqual>1</greaterOrEqual></vehicleKills>')
        self.assertEqual((True, set()), policy._condition('vehicleKills', condition, facts))
        self.receipt['interactions'][0]['mission_events'][1][3] = 100
        self.assertEqual((False, set()), policy._condition('vehicleKills', condition, facts))
        self.receipt['interactions'][0]['mission_events'][1][3] = 101
        self.assertEqual((False, set()), policy._condition('vehicleKills', condition, facts))
        condition = node('<vehicleDamage><lvlDiff>0</lvlDiff><eventCount/>'
                         '<greaterOrEqual>1</greaterOrEqual></vehicleDamage>')
        self.assertEqual((True, set()), policy._condition('vehicleDamage', condition, facts))
        self.assertEqual((False, set()), policy._condition('vehicleDamage', condition,
            policy._Facts(self.receipt, vehicles(10, 9))))


class MT11ReceiptTests(unittest.TestCase):
    state = event_fixture.MissionEventReceiptTests.state

    def test_human_ram_round_trip_restart_and_exactly_once_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state, first, second = self.state(path)
            first.vehicle, second.vehicle = 'medium', 'enemy'
            state._freeze_round_participants((first, second))
            first.health, second.health = 100, 100
            self.assertTrue(state._apply_human_ram_damage(first, second,
                {'damage_to_self': 25, 'damage_to_other': 300}, (1, 2), 1000000))
            self.assertTrue(state._finish_battle(1, 'elimination'))
            receipt = _latest_receipt(state, first.account_key)
            ram = [e for e in receipt['interactions'][0]['mission_events'] if e[0] == 'ram']
            self.assertEqual([['ram', 60000, 100, 25, True, True, False]], ram)
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = server.BattleState(map_name='01_karelia', receipt_state_path=path)
            replay = _latest_receipt(restarted, first.account_key)
            self.assertEqual(receipt, replay)
            selection = {'personalMissionSelections': {'regular': [191]},
                         'personalMissionProgress': {}}
            quest = definition('<vehicleKills><attackReason>2</attackReason>'
                '<rammingInfo>stayedAlive</rammingInfo><greaterOrEqual>1'
                '</greaterOrEqual></vehicleKills>', '<win/><isAlive/>', minimum=1)
            completed = []
            store = postbattle_store.PostBattleStore(path=None)
            store._account_key = first.account_key
            def settle(row):
                result = policy.evaluate(selection, row, vehicles(), lambda unused: quest)
                completed.append(result['completed'])
                selection['personalMissionProgress'].update(result['completed'])
                return {}
            store.set_progress_applier(settle)
            self.assertTrue(store.accept(replay))
            self.assertFalse(store.accept(replay))
            self.assertEqual([{'191': 2}], completed)

    def test_mutual_destruction_records_both_post_collision_states(self):
        state, first, second = self.state()
        first.health = second.health = 10
        state._apply_human_ram_damage(first, second,
            {'damage_to_self': 50, 'damage_to_other': 50}, (1, 2), 1000000)
        for pid in (1, 2):
            rows = state._receipt_interactions(('player', pid))
            ram = [e for e in rows[0]['mission_events'] if e[0] == 'ram']
            self.assertEqual([['ram', 60000, 10, 10, True, False, False]], ram)

    def test_worker_bot_and_human_contacts_use_settled_pair_and_deduplicate(self):
        for target_kind in ('human', 'bot'):
            for received in (25, 100):
                with self.subTest(target=target_kind, received=received):
                    state = projectile_fixture._state()
                    authority = projectile_fixture.SIMULATION_WORKER_AUTHORITY_ID
                    state.bot_manifest_authority_id = authority
                    state.tick = int((server.PREBATTLE_SECONDS + 60) * server.TICK_HZ)
                    target_id = 1 if target_kind == 'human' else 17
                    for bot_id in ((16,) if target_kind == 'human' else (16, 17)):
                        state.bot_states[bot_id] = dict(
                            id=bot_id, team=2 if bot_id == 16 else 1,
                            vehicle='ussr:R11_MS-1', health=50, max_health=1000,
                            alive=True, display_health=50, x=0., y=0., z=0.,
                            critical={}, combat_revision=0, combat_base_revision=0,
                            combat_ack_seq=0, combat_fire_elapsed=0., combat_fire_timer=0.)
                        state.bot_terminal_criticals[bot_id] = projectile_fixture._terminal_critical()
                    message = dict(round_id=state.round_id, bot_id=16,
                        target_kind=target_kind, target_id=target_id, ram_seq=1,
                        damage_to_bot=100, damage_to_target=received)
                    if target_kind == 'human':
                        target = state.players[1]
                        target.health = 50
                        target.ram_contacts[1] = projectile_fixture._player_ram_contact(bot_id=16)
                        message.update(ram_contact_player_id=1, ram_contact_seq=1)
                    self.assertTrue(state.report_bot_ram(authority, message))
                    actor = ('player' if target_kind == 'human' else 'bot', target_id)
                    before = state._receipt_interactions(actor)
                    ram = [e for e in before[0]['mission_events'] if e[0] == 'ram']
                    self.assertEqual([['ram', 60000, 50, min(50, received),
                                      True, received < 50, False]], ram)
                    self.assertTrue(state.report_bot_ram(authority, message))
                    self.assertEqual(before, state._receipt_interactions(actor))
                    self.assertTrue(mission_events.valid(before[0]))

    def test_ram_history_shares_existing_actor_budget_and_never_infers_missing_event(self):
        state, first, second = self.state()
        row = state._statistics_interaction(('player', 1), ('player', 2))
        row['mission_events'] = [
            ['damage', 60000, 1, False, 100.0, True, None]
        ] * mission_events.MAX_EVENTS
        state._apply_human_ram_damage(first, second,
            {'damage_to_self': 1, 'damage_to_other': 1}, (1, 2), 1000000)
        self.assertEqual(mission_events.MAX_EVENTS, len(row['mission_events']))
        self.assertFalse(row['mission_events_complete'])
        self.assertTrue(mission_events.valid(row))

    def test_ram_version_and_fields_are_validated_by_every_receipt_reader(self):
        state, first, second = self.state()
        state._apply_human_ram_damage(first, second,
            {'damage_to_self': 1, 'damage_to_other': second.health}, (1, 2), 1000000)
        self.assertTrue(state._finish_battle(1, 'elimination'))
        receipt = _latest_receipt(state, first.account_key)
        row = receipt['interactions'][0]
        self.assertEqual(mission_events.VERSION, row['mission_events_version'])
        for field, value in ((2, 0), (2, True), (3, -1), (4, 1), (5, None), (6, 'yes')):
            bad = copy.deepcopy(receipt)
            ram = next(e for e in bad['interactions'][0]['mission_events'] if e[0] == 'ram')
            ram[field] = value
            self.assertFalse(client._valid_battle_receipt(bad))
            with self.assertRaises(ValueError):
                server._persisted_result_receipt(bad)
            with self.assertRaises(ValueError):
                postbattle_store._receipt(bad)
        for version in (0, mission_events.VERSION + 1, True, '2'):
            bad = copy.deepcopy(receipt)
            bad['interactions'][0]['mission_events_version'] = version
            self.assertFalse(client._valid_battle_receipt(bad))
            with self.assertRaises(ValueError):
                server._persisted_result_receipt(bad)
            with self.assertRaises(ValueError):
                postbattle_store._receipt(bad)
        # Old durable receipts retain their original short event rows. A
        # version change alone must not bless a malformed v3/v4/v5 payload.
        for version in (3, mission_events.VERSION):
            short = copy.deepcopy(receipt)
            short['interactions'][0]['mission_events_version'] = version
            event = next(e for e in short['interactions'][0]['mission_events']
                         if e[0] == 'damage')
            del event[4:]
            self.assertFalse(client._valid_battle_receipt(short))
        for version in (1, 2, 3, 4):
            with self.subTest(legacy_version=version):
                legacy = copy.deepcopy(receipt)
                for interaction in legacy['interactions']:
                    interaction['mission_events_version'] = version
                    if version < 3:
                        interaction['mission_events'] = [
                            e[:4] if e[0] == 'damage' else
                            e[:5] if e[0] == 'kill' else e
                            for e in interaction['mission_events']]
                    else:
                        interaction['mission_events'] = [
                            e[:6] if e[0] == 'kill' else e
                            for e in interaction['mission_events']]
                    if version == 1:
                        # v1 had no ram rows and may omit its version field.
                        if any(e[0] == 'ram' for e in interaction['mission_events']):
                            self.assertFalse(mission_events.valid(interaction))
                        interaction.pop('mission_events_version')
                        interaction['mission_events'] = [
                            e for e in interaction['mission_events'] if e[0] != 'ram']
                    self.assertTrue(mission_events.valid(interaction))
                self.assertTrue(client._valid_battle_receipt(legacy))
                self.assertEqual(legacy, server._persisted_result_receipt(legacy))
                restored = postbattle_store._receipt(legacy)
                self.assertEqual(legacy['interactions'], restored['interactions'])


if __name__ == '__main__':
    unittest.main()
