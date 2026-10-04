"""SPG missions consume admitted events through wire and durable receipts."""

import copy
import json
from pathlib import Path
import types
import unittest
from unittest import mock

import test_port_0922_server_projectiles as projectiles
import test_port_0922_postbattle as results
import test_port_0922_personal_campaign_battle as missions


class SPGMissionTests(unittest.TestCase):
    def setUp(self):
        self.state = projectiles._state(players=6)
        for player in self.state.players.values():
            player.account_key = ('%032d' % player.player_id)
            player.vehicle = ('france:F38_Bat_Chatillon155_58'
                              if player.player_id == 1 else
                              'germany:G04_PzVI_Tiger_I')
        self.state._freeze_round_participants(self.state.players.values())
        self.policy = missions.garage_fixture._load_port_module('personal_campaign_battle')
        self.vehicles = types.SimpleNamespace(VehicleDescr=lambda typeName:
            types.SimpleNamespace(type=types.SimpleNamespace(
                tags={'SPG' if 'Bat_Chatillon' in typeName else 'heavyTank'},
                level=10)))

    def shot(self, targets=(2,), damage=1, duration=16750, shooter=1,
             replay=False):
        launch = projectiles._launch(shooter_id=shooter, is_he=True,
                                     splash_radius=100.0)
        self.assertTrue(projectiles._launch_authority(self.state, launch))
        effects = []
        for index, target in enumerate(targets):
            effect = projectiles._effect(
                target_id=target, damage=damage, shot_result=1,
                target_pose=(10.0, 0.0, 0.0) if index else None)
            if duration is not None:
                effect.update(stun_end_server_time_ms=(
                    self.state._server_time_ms() + duration),
                    stun_duration_ms=duration)
            self.assertIsNotNone(results.lan_client_module._strict_projectile_effect(effect))
            effects.append(effect)
        terminal = projectiles._resolve(
            '1:p:%d:%d' % (shooter, launch['shot_seq']),
            direct=effects[0], splash=effects[1:])
        self.assertTrue(self.state.resolve_projectile(
            projectiles.SIMULATION_WORKER_AUTHORITY_ID, terminal))
        if replay:
            before = copy.deepcopy(self.state.vehicle_statistics)
            self.assertTrue(self.state.resolve_projectile(
                projectiles.SIMULATION_WORKER_AUTHORITY_ID, terminal))
            self.assertEqual(before, self.state.vehicle_statistics)
        return terminal

    def receipt(self):
        self.assertTrue(self.state._finish_battle(1, 'team_eliminated'))
        raw = results._latest_receipt(self.state, self.state.players[1].account_key)
        self.assertTrue(results.lan_client_module._valid_battle_receipt(raw))
        # The save/restart representation must preserve fractional seconds
        # and event counts rather than silently dropping the new fields.
        canonical = results.postbattle_store._receipt(json.loads(json.dumps(raw)))
        self.assertEqual(raw['stats'], canonical['stats'])
        self.assertEqual(raw['interactions'], canonical['interactions'])
        return canonical

    def evaluate(self, receipt, main, additional='<win/>', qid=61):
        definition = missions.definition(main, additional, tags='SPG')
        return self.policy.evaluate(
            {'personalMissionSelections': {'regular': [qid]}}, receipt,
            self.vehicles, lambda unused: definition)

    def test_spg1_three_hits_same_target_complete_honours_and_survive_retry(self):
        for unused in range(3):
            self.shot(replay=True)
        receipt = self.receipt()
        self.assertEqual(3, receipt['stats']['stun_num'])
        self.assertEqual(1, receipt['stats']['stunned'])
        self.assertEqual(50250, receipt['stats']['stun_duration_ms'])
        self.assertEqual(50.25, receipt['interactions'][0]['stun_duration'])
        self.assertEqual(3, receipt['interactions'][0]['damage_events'])
        outcome = self.evaluate(receipt,
            '<results><key>stunNum</key><greaterOrEqual>3</greaterOrEqual></results>',
            '<vehicleDamage><eventCount/><greaterOrEqual>3</greaterOrEqual></vehicleDamage>')
        self.assertEqual({'61': 2}, outcome['completed'])
        self.assertEqual({}, outcome['unsupported'])
        with mock.patch.dict('sys.modules', {'battle_results_shared':
                types.SimpleNamespace(VehicleInteractionDetails=results._InteractionDetails)}):
            native = results._packed_vehicle(receipt)
        self.assertEqual(3, native['stunNum'])
        self.assertEqual(50.25, native['stunDuration'])
        for details in results._InteractionDetails.instances[-1].rows.values():
            self.assertNotIn(None, details)
            self.assertNotIn('damage_events', details)
            self.assertEqual(50.25, details['stunDuration'])

    def test_zero_damage_stuns_complete_main_but_not_damage_honours(self):
        for unused in range(3):
            self.shot(damage=0)
        receipt = self.receipt()
        outcome = self.evaluate(receipt,
            '<results><key>stunNum</key><greaterOrEqual>3</greaterOrEqual></results>',
            '<vehicleDamage><eventCount/><greaterOrEqual>3</greaterOrEqual></vehicleDamage>')
        self.assertEqual({'61': 1}, outcome['completed'])
        self.assertEqual(0, receipt['interactions'][0]['damage_events'])

    def test_one_explosion_is_one_multi_stun_event_even_with_three_targets(self):
        self.shot(targets=(2, 4, 6))
        row = self.state._statistics_row('player', 1)
        self.assertEqual((1, 1), (row['stun_shots_2'], row['stun_shots_3']))
        condition = ('<multiStunEvent><stunnedByShot>2</stunnedByShot>'
                     '<greaterOrEqual>2</greaterOrEqual></multiStunEvent>')
        # Three single-target stuns in total do not prove two multi-target shots.
        self.shot(targets=(2,))
        self.assertEqual(1, row['stun_shots_2'])
        self.shot(targets=(2, 4))
        receipt = self.receipt()
        self.assertEqual({'61': 2}, self.evaluate(receipt, condition)['completed'])
        receipt['stats']['stun_shots_2'] = 1
        self.assertEqual({}, self.evaluate(receipt, condition)['completed'])

    def test_friendly_hits_dead_targets_and_plain_damage_never_become_stuns(self):
        self.shot(targets=(3,), damage=0)
        self.shot(duration=None)
        self.state.players[4].alive = False
        self.state.players[4].health = 0
        self.shot(targets=(4,), damage=0)
        self.assertEqual(0, self.state._statistics_row('player', 1)['stun_num'])

    def test_healing_and_shorter_overlap_preserve_committed_hits_and_owner(self):
        self.shot(duration=20000)
        self.shot(duration=5000, shooter=3)
        self.assertEqual(('player', 1), self.state._active_stun_assister(('player', 2)))
        self.assertEqual(1, self.state._statistics_row('player', 3)['stun_num'])
        self.state._clear_vehicle_stun(('player', 2))
        self.assertEqual(20000, self.state._statistics_row('player', 1)['stun_duration_ms'])
        self.assertIsNone(self.state._active_stun_assister(('player', 2)))

    def test_stun_and_track_kill_assists_use_live_owners_once(self):
        self.shot(damage=0)
        victim = ('player', 2)
        self.state.track_immobilisers[victim] = ('player', 1)
        critical = {'destroyed': ['leftTrackHealth']}
        self.state.players[2].health = 0
        self.state.players[2].alive = False
        self.state._record_damage(('player', 3), victim, 1000, critical)
        self.state._record_impairment_kill_assists(('player', 3), victim, critical)
        row = self.state._statistics_row('player', 1)
        self.assertEqual((1, 1), (row['kills_assisted_stun'], row['kills_assisted_track']))
        self.assertEqual(1000, row['damage_assisted_stun'])

    def test_own_kills_and_expired_stuns_do_not_earn_stun_kill_assists(self):
        self.shot(damage=0)
        self.state.players[2].alive = False
        self.state._record_damage(('player', 1), ('player', 2), 1000, {})
        self.assertEqual(0, self.state._statistics_row('player', 1)['kills_assisted_stun'])
        self.state._clear_vehicle_stun(('player', 2))
        self.state._record_impairment_kill_assists(('player', 3), ('player', 2), {})
        self.assertEqual(0, self.state._statistics_row('player', 1)['kills_assisted_stun'])

    def test_spotted_history_does_not_reset_when_visibility_expires(self):
        self.assertEqual(1, self.state._statistics_row('player', 1)['not_spotted'])
        self.state.player_spotted = {2: {('player', 1)}}
        self.state._commit_detections()
        self.state.player_spotted = {}
        self.state._commit_detections()
        self.assertEqual(0, self.state._statistics_row('player', 1)['not_spotted'])

    def test_target_class_duration_and_count_are_independent_of_damage(self):
        self.shot(damage=0, duration=10001)
        self.shot(damage=0, duration=19999)
        receipt = self.receipt()
        for suffix, threshold in (('', 30), ('<eventCount/>', 2)):
            condition = ('<vehicleStun><classes>heavyTank AT-SPG</classes>' +
                         suffix + '<greaterOrEqual>%s</greaterOrEqual></vehicleStun>' % threshold)
            self.assertEqual({'61': 2}, self.evaluate(receipt, condition)['completed'])
            receipt['public_results'][1]['vehicle'] = 'france:F38_Bat_Chatillon155_58'
            self.assertEqual({}, self.evaluate(receipt, condition)['completed'])
            receipt['public_results'][1]['vehicle'] = 'germany:G04_PzVI_Tiger_I'

    def test_receipt_rejects_nonfinite_duration_and_keeps_old_receipts_readable(self):
        self.shot()
        receipt = self.receipt()
        for invalid in (float('nan'), float('inf'), True, '16.75', -1,
                        65536, 10 ** 1000):
            raw = copy.deepcopy(receipt)
            raw.update(type='battle_receipt', protocol=5)
            raw['interactions'][0]['stun_duration'] = invalid
            self.assertFalse(results.lan_client_module._valid_battle_receipt(raw))
            with self.assertRaises(ValueError):
                results.postbattle_store._receipt(raw)
        for field in ('damage_events', 'kills_assisted_stun', 'kills_assisted_track'):
            del receipt['interactions'][0][field]
        legacy = results.postbattle_store._receipt(receipt)
        outcome = self.evaluate(legacy, '<vehicleDamage><eventCount/>'
                                '<greaterOrEqual>1</greaterOrEqual></vehicleDamage>')
        self.assertEqual({}, outcome['completed'])
        self.assertIn('eventCount', outcome['unsupported']['61'][0])

    def test_all_sixty_reference_spg_expressions_have_a_supported_solo_path(self):
        # Regional 0.9.22 expressions test grammar coverage only. Runtime
        # thresholds and metadata still come from the installed #1513 XML.
        fixtures = json.loads((Path(__file__).parent / 'fixtures' /
                              'spg_mission_conditions_0922.json').read_text())
        self.assertEqual(60, len(fixtures))
        self.shot(targets=(2, 4, 6))
        receipt = self.receipt()
        receipt['stats'].update(stun_num=30, stun_duration_ms=300000,
            stunned=15, critical_hits=20, kills_assisted_stun=5,
            kills_assisted_track=0, stun_shots_2=3, stun_shots_3=3,
            damage=5000, assist_stun=4000, assist_track=4000, not_spotted=1)
        receipt['rewards']['xp'] = 100000
        for event in receipt['interactions']:
            event.update(damage_events=10, stun_num=10, stun_duration=100.0,
                         target_kills=2, assist_stun=1000, assist_track=1000)
        for qid, expression in fixtures.items():
            with self.subTest(qid=qid):
                definition = missions.definition('<win/>', tags='SPG')
                for stage in ('main', 'add'):
                    definition[stage] = missions.node('<quest><conditions>' +
                        expression[stage] + '</conditions></quest>')
                outcome = self.policy.evaluate(
                    {'personalMissionSelections': {'regular': [int(qid)]}},
                    receipt, self.vehicles, lambda unused: definition)
                self.assertEqual({qid: 2}, outcome['completed'])
                self.assertEqual({}, outcome['unsupported'])


if __name__ == '__main__':
    unittest.main()
