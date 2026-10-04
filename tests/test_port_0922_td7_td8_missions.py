"""TD7 needs HP at the kill; TD8 scales damage by the frozen maximum HP."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from test_port_0922_personal_campaign_battle import node, definition
import test_port_0922_mission_events as event_fixture
import test_port_0922_server_projectiles as projectile_fixture
from test_port_0922_postbattle import (
    _latest_receipt, postbattle_store, lan_server_module as server,
    lan_client_module as client)
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy
from gui.mods.offline_lan_0922 import mission_events


DEFINITIONS = json.loads((Path(__file__).parent /
    'fixtures/personal_mission_td789_conditions_0922.json').read_text())['missions']


class TD7TD8ConditionTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(player_id=1, vehicle='ussr:td', team=1, winner=1,
            death_reason=-1, max_health=1000, health=1,
            stats={'damage': 5000, 'damage_received': 999},
            public_results=[], interactions=[])
        for target in range(2, 6):
            self.receipt['public_results'].append(dict(actor_kind='bot', actor_id=target,
                team=2, vehicle='ussr:enemy'))
            self.receipt['interactions'].append(dict(target_kind='bot', target_id=target,
                target_kills=1, death_reason=0, mission_events_version=5,
                mission_events_complete=True,
                mission_events=[['kill', 1000, 0, False, 100., True, True]]))

    def check(self, quest, stage='main'):
        return policy._postbattle({'children': [('conditions', node(quest[stage]))]},
                                 policy._Facts(self.receipt, None))

    def test_all_four_operations_td7_td8_main_and_honours(self):
        for quest in DEFINITIONS:
            if quest['number'] == 9:
                continue
            for stage in ('main', 'add'):
                with self.subTest(name=quest['name'], stage=stage):
                    self.assertEqual((True, set()), self.check(quest, stage))

    def test_td7_counts_only_full_health_kills_at_each_boundary(self):
        for quest in DEFINITIONS:
            if quest['number'] != 7:
                continue
            threshold = quest['operation']
            for index, interaction in enumerate(self.receipt['interactions']):
                interaction['mission_events'][0][6] = index < threshold - 1
            self.assertEqual((False, set()), self.check(quest))
            self.receipt['interactions'][threshold - 1]['mission_events'][0][6] = True
            self.assertEqual((True, set()), self.check(quest))
        # End-of-battle totals cannot manufacture full-health kills.
        self.receipt['health'] = self.receipt['max_health']
        self.receipt['stats']['damage_received'] = 0
        for interaction in self.receipt['interactions']:
            interaction['mission_events'][0][6] = False
        self.assertEqual((False, set()), self.check(DEFINITIONS[0]))

    def test_td7_legacy_incomplete_missing_and_invalid_full_health_stay_unknown(self):
        original = copy.deepcopy(self.receipt)
        quest = DEFINITIONS[0]
        for mode in ('legacy', 'truncated', 'unavailable', 'invalid'):
            self.receipt = copy.deepcopy(original)
            for interaction in self.receipt['interactions']:
                if mode == 'legacy':
                    interaction['mission_events_version'] = 4
                    interaction['mission_events'][0].pop()
                elif mode == 'truncated':
                    interaction['mission_events_complete'] = False
                else:
                    interaction['mission_events'][0][6] = None if mode == 'unavailable' else 1
            self.assertIsNone(self.check(quest)[0], mode)

    def test_td7_does_not_count_friendly_kills_or_relax_flag_modifiers(self):
        for row in self.receipt['public_results']:
            row['team'] = 1
        self.assertEqual((False, set()), self.check(DEFINITIONS[0]))
        malformed = node('<vehicleKills><whileFullHealth>true</whileFullHealth>'
                         '<greaterOrEqual>1</greaterOrEqual></vehicleKills>')
        self.assertIsNone(policy._condition('vehicleKills', malformed,
            policy._Facts(self.receipt, None))[0])

    def test_td8_uses_frozen_full_health_at_inclusive_boundary(self):
        for quest in DEFINITIONS:
            if quest['number'] != 8:
                continue
            self.receipt['max_health'] = 4090
            threshold = 4090 * (quest['operation'] + 1)
            self.receipt['stats']['damage'] = threshold - 1
            self.assertEqual((False, set()), self.check(quest))
            self.receipt['stats']['damage'] = threshold
            self.assertEqual((True, set()), self.check(quest))
            self.receipt.pop('max_health')
            self.assertIsNone(self.check(quest)[0])

    def test_honours_do_not_block_main_completion_after_death_or_defeat(self):
        for quest in DEFINITIONS:
            if quest['number'] == 9:
                continue
            self.receipt['winner'] = 2
            self.assertEqual((True, set()), self.check(quest))
            self.assertEqual((False, set()), self.check(quest, 'add'))
            self.receipt['winner'] = 1
            self.receipt['death_reason'] = 0
            self.assertEqual((True, set()), self.check(quest))
            expected = quest['number'] == 7 and quest['operation'] < 3
            self.assertEqual((expected, set()), self.check(quest, 'add'))
            self.receipt['death_reason'] = -1


class TD7ReceiptTests(unittest.TestCase):
    state = event_fixture.MissionEventReceiptTests.state

    def kill_events(self, state, player_id=1):
        return [event for row in state._receipt_interactions(('player', player_id))
                for event in row['mission_events'] if event[0] == 'kill']

    def test_server_round_trip_retains_kill_time_hp_after_later_damage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state, player, enemy = self.state(path)
            # A damaged module does not reduce hull HP or fail TD7.
            player.critical = {'destroyed': ['leftTrackHealth']}
            enemy.health, enemy.alive = 0, False
            state._record_frag('player', 1, 2, 'player', 2)
            self.assertTrue(self.kill_events(state)[0][6])
            player.health = 1
            state._finish_battle(1, 'elimination')
            receipt = _latest_receipt(state, player.account_key)
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = server.BattleState(map_name='01_karelia', receipt_state_path=path)
            replay = _latest_receipt(restarted, player.account_key)
            self.assertEqual(receipt, replay)
            self.assertTrue(postbattle_store._receipt(replay)['interactions'][0]
                            ['mission_events'][0][6])
            selection = {'personalMissionSelections': {'regular': [33]},
                         'personalMissionProgress': {}}
            quest = definition('<vehicleKills><whileFullHealth/>'
                '<greaterOrEqual>1</greaterOrEqual></vehicleKills>', '<win/>', minimum=1)
            vehicles = types.SimpleNamespace(VehicleDescr=lambda **unused:
                types.SimpleNamespace(type=types.SimpleNamespace(tags={'mediumTank'}, level=10)))
            completed = []
            store = postbattle_store.PostBattleStore(path=None)
            store._account_key = player.account_key
            def settle(row):
                result = policy.evaluate(selection, row, vehicles, lambda unused: quest)
                completed.append(result['completed'])
                selection['personalMissionProgress'].update(result['completed'])
                return {}
            store.set_progress_applier(settle)
            self.assertTrue(store.accept(replay))
            self.assertFalse(store.accept(replay))
            self.assertEqual([{'33': 2}], completed)

    def test_module_repair_cannot_restore_hull_full_health(self):
        state, player, enemy = self.state()
        player.health -= 1
        player.critical = {}
        enemy.health, enemy.alive = 0, False
        state._record_frag('player', 1, 2, 'player', 2)
        self.assertFalse(self.kill_events(state)[0][6])

    def test_full_health_requires_living_actor_and_frozen_maximum(self):
        state, player, unused = self.state()
        player.max_health = player.health = 500
        self.assertFalse(state._mission_full_health(('player', 1)))
        player.health = 1000
        self.assertTrue(state._mission_full_health(('player', 1)))
        player.alive = False  # Crew destruction may leave hull HP untouched.
        self.assertFalse(state._mission_full_health(('player', 1)))
        state.bot_states[3] = dict(health=750, max_health=750, alive=True)
        self.assertTrue(state._mission_full_health(('bot', 3)))
        state.bot_states[3]['health'] = 749
        self.assertFalse(state._mission_full_health(('bot', 3)))
        state.bot_states[3].pop('max_health')
        self.assertIsNone(state._mission_full_health(('bot', 3)))

    def test_ram_full_health_uses_both_settled_damage_halves(self):
        for damage in (0, 1, 1000):
            state, player, enemy = self.state()
            state._apply_human_ram_damage(player, enemy,
                {'damage_to_self': damage, 'damage_to_other': 1000}, (1, 2), 1000000)
            self.assertEqual(damage == 0, self.kill_events(state)[0][6])

    def test_missing_attacker_state_is_unknown_instead_of_assumed_full(self):
        state, player, enemy = self.state()
        enemy.health, enemy.alive = 0, False
        state.players.pop(1)
        state._record_frag('player', 1, 2, 'player', 2)
        self.assertIsNone(self.kill_events(state)[0][6])

    def test_he_kill_and_self_splash_share_the_settled_health_state(self):
        for self_damage in (0, 40):
            state = projectile_fixture._state()
            state.players[2].health = 100
            self.assertTrue(projectile_fixture._launch_authority(state,
                projectile_fixture._launch(is_he=True, splash_radius=15.0,
                                           penetration_factor=0.0)))
            message = projectile_fixture._resolve('1:p:1:1', penetration_factor=0.0,
                direct=projectile_fixture._effect(damage=100),
                splash=[projectile_fixture._effect(target_id=1, damage=self_damage,
                    x=10., target_pose=(0., 1., 0.))])
            self.assertTrue(state.resolve_projectile(
                projectile_fixture.SIMULATION_WORKER_AUTHORITY_ID, message),
                state.last_projectile_resolve_reject)
            self.assertEqual(self_damage == 0, self.kill_events(state)[0][6])
            self.assertEqual(1000 - self_damage, state.players[1].health)
            self.assertTrue(state.resolve_projectile(
                projectile_fixture.SIMULATION_WORKER_AUTHORITY_ID, message))
            self.assertEqual(1, len(self.kill_events(state)))

    def test_fire_kill_uses_health_at_burn_not_at_ignition(self):
        for later_damage in (0, 1):
            state = projectile_fixture._state()
            enemy = projectile_fixture.ServerProjectileLedgerTests._ignite_player_with_projectile(
                state, False)
            state.players[1].health -= later_damage
            with mock.patch.object(server.player_critical_mechanics, 'advance_fire',
                return_value={'damage': 5, 'critical': enemy.critical,
                              'fire_elapsed': 1., 'fire_timer': 0.}):
                state._tick_player_fire(1.)
            self.assertFalse(enemy.alive)
            self.assertEqual(later_damage == 0, self.kill_events(state)[0][6])

    def test_invalid_full_health_event_rejected_by_all_receipt_readers(self):
        state, player, enemy = self.state()
        enemy.health, enemy.alive = 0, False
        state._record_frag('player', 1, 2, 'player', 2)
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, player.account_key)
        for value in (0, 1, 0.0, '', [], {}):
            bad = copy.deepcopy(receipt)
            bad['interactions'][0]['mission_events'][0][6] = value
            self.assertFalse(client._valid_battle_receipt(bad), value)
            with self.assertRaises(ValueError):
                server._persisted_result_receipt(copy.deepcopy(bad))
            with self.assertRaises(ValueError):
                postbattle_store._receipt(bad)


if __name__ == '__main__':
    unittest.main()
