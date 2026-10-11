"""Spotting, ignition, view-range and mounted-device mission receipts."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest

from test_port_0922_personal_campaign_battle import node
from test_port_0922_mission_events import MissionEventReceiptTests
from test_port_0922_postbattle import _latest_receipt, postbattle_store
from test_port_0922_bot_runtime import ServerBotObservationRelayTests
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy
from gui.mods.offline_lan_0922 import mission_events
from test_port_0922_postbattle import lan_server_module as server, lan_client_module as client


class MissionVisibilityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.definitions = json.loads((Path(__file__).parent /
            'fixtures/personal_mission_visibility_conditions_0922.json').read_text())['missions']
        self.receipt = dict(player_id=1, vehicle='ussr:medium', team=1, winner=1,
            death_reason=-1, battle_mode='regular', rewards={'xp': 1000},
            vehicle_compact_descr=base64.b64encode(b'coatedOptics camouflageNet').decode('ascii'),
            stats={'damage': 6000, 'kills_assisted_track': 3},
            public_results=[dict(actor_kind='player', actor_id=1, team=1,
                                 vehicle='ussr:medium', xp=1000)], interactions=[])
        for target in range(2, 8):
            self.receipt['public_results'].append(dict(actor_kind='bot', actor_id=target,
                team=2, vehicle='ussr:medium', xp=100))
            self.receipt['interactions'].append(dict(target_kind='bot', target_id=target,
                damage=1000, damage_events=1, target_kills=1, death_reason=0,
                assist_radio=1, assist_track=0, kills_assisted_radio=0,
                mission_events_version=3, mission_events_complete=True, mission_events=[
                    ['spot', 1000, True], ['damage', 2000, 1000, False, 300., True, 400.],
                    ['fire', 2000, 1], ['kill', 3000, 0, False, 300., True]]))
        self.vehicles = types.SimpleNamespace(VehicleDescr=self.describe)

    @staticmethod
    def describe(typeName=None, compactDescr=None):
        return types.SimpleNamespace(type=types.SimpleNamespace(name='ussr:medium',
            tags={'mediumTank'}, level=10), optionalDevices=[
                types.SimpleNamespace(name=name) for name in
                (compactDescr.decode('ascii').split() if compactDescr else ())])

    def check(self, definition, stage='main'):
        return policy._postbattle({'children': [('conditions', node(definition[stage]))]},
                                   policy._Facts(self.receipt, self.vehicles))

    def test_all_four_operation_main_and_additional_conditions(self):
        for definition in self.definitions:
            for stage in ('main', 'add'):
                with self.subTest(name=definition['name'], stage=stage):
                    self.assertEqual((True, set()), self.check(definition, stage))

    def test_view_range_zero_is_dynamic_radius_and_never_unlimited(self):
        for definition in self.definitions:
            if definition['chain'] != 2:
                continue
            for row in self.receipt['interactions']:
                row['mission_events'][1][4] = 400.001
            self.assertEqual((False, set()), self.check(definition))
            for row in self.receipt['interactions']:
                row['mission_events'][1][4] = 400.
            self.assertEqual((True, set()), self.check(definition))
            # At 450 m, standard 400 m vision fails but armed binoculars pass.
            for row in self.receipt['interactions']:
                row['mission_events'][1][4:7] = [450., True, 500.]
            self.assertEqual((True, set()), self.check(definition))
            for row in self.receipt['interactions']:
                row['mission_events'][1][6] = None
            self.assertIsNone(self.check(definition)[0])
            for row in self.receipt['interactions']:
                row['mission_events'][1][6] = 400.

    def test_lt9_counts_first_detections_before_being_spotted_not_battle_end(self):
        for definition in self.definitions:
            if definition['chain'] != 1 or definition['number'] != 9:
                continue
            threshold = (1, 2, 4, 6)[definition['operation'] - 1]
            self.receipt['stats']['not_spotted'] = 0
            for index, row in enumerate(self.receipt['interactions']):
                row['mission_events'][0][2] = index < threshold - 1
            self.assertEqual((False, set()), self.check(definition))
            self.receipt['interactions'][threshold - 1]['mission_events'][0][2] = True
            self.assertEqual((True, set()), self.check(definition))

    def test_td4_uses_visibility_at_kill_and_does_not_infer_from_end_stat(self):
        self.receipt['stats']['not_spotted'] = 0
        for definition in self.definitions:
            if definition['chain'] != 4:
                continue
            for row in self.receipt['interactions']:
                row['mission_events'][-1][5] = False
            self.assertEqual((False, set()), self.check(definition))
            for row in self.receipt['interactions']:
                row['mission_events'][-1][5] = True
            self.assertEqual((True, set()), self.check(definition))

    def test_fire_started_counts_ignitions_even_without_hull_damage(self):
        for row in self.receipt['interactions']:
            row.update(damage=0, damage_events=0)
            row['mission_events'] = [['fire', 2000, 1]]
        for definition in self.definitions:
            if definition['chain'] != 3:
                continue
            self.assertEqual((True, set()), self.check(definition))
        for row in self.receipt['interactions']:
            row.update(damage=1000, damage_events=1)
            row['mission_events'] = [['damage', 2000, 1000, False, 100., True, 400.]]
        for definition in self.definitions:
            if definition['chain'] == 3:
                self.assertEqual((False, set()), self.check(definition))

    def test_lt6_optics_bond_optics_and_binoculars_use_frozen_loadout(self):
        definition = next(d for d in self.definitions if d['qid'] == 6)
        for device in ('coatedOptics', 'deluxCoatedOptics', 'stereoscope'):
            self.receipt['vehicle_compact_descr'] = base64.b64encode(device.encode('ascii')).decode('ascii')
            self.assertEqual((True, set()), self.check(definition, 'add'))
        for device in ('coatedOpticsBattleBooster', 'camouflageNet', ''):
            self.receipt['vehicle_compact_descr'] = base64.b64encode((device or 'empty').encode('ascii')).decode('ascii')
            self.assertEqual((False, set()), self.check(definition, 'add'))
        self.receipt.pop('vehicle_compact_descr')
        self.assertIsNone(self.check(definition, 'add')[0])
        self.assertEqual((True, set()), self.check(definition))

    def test_legacy_evidence_stays_unknown_for_new_filters(self):
        for row in self.receipt['interactions']:
            row['mission_events_version'] = 2
            row['mission_events'] = [['damage', 2000, 1000, False],
                                     ['kill', 3000, 0, False, 300.]]
        for definition in self.definitions:
            if definition['chain'] == 1 and definition['number'] == 6:
                continue
            self.assertIsNone(self.check(definition)[0], definition['name'])


class MissionVisibilityReceiptTests(unittest.TestCase):
    state = MissionEventReceiptTests.state

    def test_descriptor_and_v3_evidence_survive_server_restart_and_client_store(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state, player, enemy = self.state(path)
            frozen = base64.b64encode(b'coatedOptics').decode('ascii')
            player.vehicle_compact_descr = frozen
            state._freeze_round_participants(tuple(state.players.values()))
            player.vehicle_compact_descr = base64.b64encode(b'stereoscope').decode('ascii')
            state.player_mission_view_ranges[1] = 425.
            state.player_mission_view_ranges_tick = state.tick
            state._record_damage(('player', 1), ('player', 2), 100, {})
            state._finish_battle(1, 'elimination')
            receipt = _latest_receipt(state, player.account_key)
            self.assertEqual(frozen, receipt['vehicle_compact_descr'])
            self.assertEqual([100., True, 425.], receipt['interactions'][0]['mission_events'][0][4:])
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = server.BattleState(map_name='01_karelia', receipt_state_path=path)
            replay = _latest_receipt(restarted, player.account_key)
            self.assertEqual(receipt, replay)
            self.assertEqual(frozen, postbattle_store._receipt(replay)['vehicle_compact_descr'])
            for invalid in ('', '?', 'Y', 'YQ', 'YQ==\n', None):
                bad = dict(receipt, vehicle_compact_descr=invalid)
                self.assertFalse(client._valid_battle_receipt(bad))
                with self.assertRaises(ValueError):
                    postbattle_store._receipt(bad)
                with self.assertRaises(ValueError):
                    server._persisted_result_receipt(bad)

    def test_ignition_transition_deduplication_extinguishing_and_reignition(self):
        state, unused_player, unused_enemy = self.state()
        actor, target = ('player', 1), ('player', 2)
        lit = {'fire': True}
        state._record_critical_damage(actor, target, {}, lit)
        state._record_critical_damage(actor, target, lit, lit)
        state._record_critical_damage(actor, target, lit, {'fire': False})
        state._record_critical_damage(actor, target, {'fire': False}, lit)
        self.assertEqual([['fire', 60000, 1], ['fire', 60000, 1]],
                         state._receipt_interactions(actor)[0]['mission_events'])

    def test_simultaneous_discovery_and_later_first_spot_have_no_team_order_bias(self):
        state, unused_player, unused_enemy = self.state()
        state.player_spotted = {1: {('player', 2)}, 2: {('player', 1)}}
        state._commit_detections()
        for actor in (('player', 1), ('player', 2)):
            self.assertEqual([['spot', 60000, False]],
                             state._receipt_interactions(actor)[0]['mission_events'])
        # A later unspotted kill qualifies for TD4 even after an earlier spot.
        state.player_spotted = {}
        state.team_visible_targets = {1: set(), 2: set()}
        self.assertTrue(state._mission_invisible(('player', 1)))
        state.team_lit_targets[2] = {('player', 1): server.time.monotonic() + 10.0}
        self.assertFalse(state._mission_invisible(('player', 1)))

    def test_validated_worker_view_radius_reaches_damage_history(self):
        state, unused_a, unused_b = ServerBotObservationRelayTests._server()
        message = ServerBotObservationRelayTests._human_message(state.round_id)
        message['player_vision_ranges'] = [{'id': 1, 'radius': 472.5}]
        self.assertTrue(state.update_bot_observation(server.SIMULATION_WORKER_AUTHORITY_ID, message))
        state._record_damage(('player', 1), ('bot', 11), 200, {})
        self.assertEqual(472.5, state._receipt_interactions(('player', 1))[0]['mission_events'][-1][6])
        before = copy.deepcopy(state.player_mission_view_ranges)
        message['player_vision_ranges'][0]['radius'] = float('nan')
        self.assertFalse(state.update_bot_observation(server.SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(before, state.player_mission_view_ranges)

    def test_view_range_evidence_expires_and_missing_observer_is_not_reused(self):
        state, unused_a, unused_b = ServerBotObservationRelayTests._server()
        message = ServerBotObservationRelayTests._human_message(state.round_id)
        message['player_vision_ranges'] = [{'id': 1, 'radius': 500.}]
        self.assertTrue(state.update_bot_observation(server.SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(500., state._mission_view_range(('player', 1)))
        state.tick += server.MISSION_VIEW_RANGE_STALE_TICKS + 1
        self.assertIsNone(state._mission_view_range(('player', 1)))
        message['player_vision_ranges'] = []
        self.assertTrue(state.update_bot_observation(server.SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertIsNone(state._mission_view_range(('player', 1)))
        message['player_vision_ranges'] = [{'id': 1, 'radius': 400.}]
        self.assertTrue(state.update_bot_observation(server.SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(400., state._mission_view_range(('player', 1)))
        state._elect_bot_authority()
        self.assertIsNone(state._mission_view_range(('player', 1)))

    def test_worker_observation_uses_the_same_dynamic_radius_as_spotting(self):
        import test_port_0922_bot_runtime as fixture
        runtime = fixture._load().BotRuntime(
            1, descriptor_resolver=lambda unused: fixture._combat_descriptor())
        player = fixture._admit_player({
            'id': 2, 'team': 1, 'alive': True, 'x': 0., 'y': 0., 'z': 0.})
        player['effective_params']['spotting']['has_binoculars'] = True
        player['effective_params']['spotting']['binocular_factor'] = 1.25
        first, armed = {}, {}
        runtime._append_human_observations([player], 1., {}, {}, first)
        runtime._append_human_observations([player], 4., {}, {}, armed)
        moving = first['player_vision_ranges'][0]
        still = armed['player_vision_ranges'][0]
        self.assertEqual(2, moving['id'])
        self.assertAlmostEqual(moving['radius'] * 1.25, still['radius'])
