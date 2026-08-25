import json
import re
import sys
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import lan_client as lan_client_module
from gui.mods.offline_lan_0922.lan_client import (
    HUMAN_RAM_TIMELINE_CAPABILITY, LANClient,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY, _strict_projectile_effect,
    project_bot_state)
from gui.mods.offline_lan_0922.authority_worker import (
    AuthorityWorkerLANClient)
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync
from effective_params_fixture import effective_params


def _player_equipment_contract():
    return {
        'equipment_states': [],
        'equipment_revision': 0,
        'equipment_intent_seq': 0,
        'equipment_intent_result': {
            'intent_seq': 0, 'accepted': False, 'reason': ''},
    }


def _snapshot_player(player_id=1, **changes):
    player = {
        'id': player_id,
        'critical_revision': 0,
        'critical_base_revision': 0,
        'critical_ack_seq': 0,
        'input_seq': 0,
        'up_cosine': 1.0,
        'landing_observation_seq': 0,
    }
    player.update(_player_equipment_contract())
    player.update(changes)
    return player


class LanProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
        self.client.ready = True
        self.client.phase = 'battle'
        self.client.round_id = 7
        self.client.state_revision = 4
        self.client.player_id = 1
        self.client.host_player_id = 1
        self.client.bot_authority_id = 0
        self.client.vehicle_compact_descr = 'dGVzdA=='
        self.client.effective_params = effective_params()
        self.client._published_player_effective_params[1] = \
            effective_params()
        self.sent = []
        def send(message, **unused_options):
            self.sent.append(message)
            return True
        self.client._send = send

    def _worker_client(self):
        worker = AuthorityWorkerLANClient('127.0.0.1', 28782)
        worker.ready = True
        worker.phase = 'battle'
        worker.round_id = self.client.round_id
        worker.bot_authority_id = worker.player_id
        worker._send = self.client._send
        return worker

    def test_ram_contact_ledger_requires_result_aware_v3_contract(self):
        self.assertEqual(
            'ram_contact_ledger_v3',
            lan_client_module.RAM_CONTACT_LEDGER_CAPABILITY)
        self.assertIn(
            lan_client_module.RAM_CONTACT_LEDGER_CAPABILITY,
            lan_client_module.CLIENT_CAPABILITIES)

    def test_he_explosion_evidence_is_a_server_only_capability(self):
        self.assertEqual(
            'he_explosion_evidence_v1',
            lan_client_module.HE_EXPLOSION_EVIDENCE_CAPABILITY)
        self.assertNotIn(
            lan_client_module.HE_EXPLOSION_EVIDENCE_CAPABILITY,
            lan_client_module.CLIENT_CAPABILITIES)

    def test_v5_explicit_control_messages(self):
        self.assertTrue(self.client.leave_battle())
        critical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        self.assertFalse(self.client.send_hit(
            2, 4, 500, 2, 1, (1, 2, 3), critical,
            critical_target_base_revision=8,
            critical_target_ack_seq=3, hull_damage=120))
        worker = self._worker_client()
        self.assertTrue(worker.send_bot_manifest([{'id': 1}] * 40))
        self.assertTrue(worker.send_bot_state([{
            'id': 1, 'reload_time': 0.5, 'reload_duration': 0.5}]))
        self.assertTrue(worker.send_bot_observation([{}] * 70, [{}] * 20))
        self.assertTrue(worker.send_bot_bot_hit(1, 2, 3, 120, 2))
        self.assertTrue(worker.send_bot_ram(
            1, 'human', 2, 4, 620, 880))
        self.assertTrue(worker.send_rules_state({'1': {'points': 10}}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 17,
            'item_index': 4, 'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.5, 'speed': 12.0, 'is_shot': False}))
        self.assertTrue(worker.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 17,
            'item_index': 4, 'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.5, 'speed': 12.0, 'is_shot': False}))
        self.assertTrue(worker.send_battle_result(1, 'elimination'))
        self.assertEqual('leave_battle', self.sent[0]['type'])
        self.assertEqual(30, len(self.sent[1]['bots']))
        self.assertEqual(64, len(self.sent[3]['contacts']))
        self.assertEqual(1, self.sent[4]['attacker_bot'])
        self.assertEqual('bot_ram_report', self.sent[5]['type'])
        self.assertEqual(620, self.sent[5]['damage_to_bot'])
        self.assertEqual(880, self.sent[5]['damage_to_target'])
        self.assertEqual('rules_state', self.sent[6]['type'])
        self.assertEqual('destructible', self.sent[7]['type'])
        self.assertEqual('tree', self.sent[7]['destructible_kind'])
        self.assertEqual(17, self.sent[7]['chunk_id'])
        self.assertTrue(all(message['round_id'] == 7
                            for message in self.sent))

    def test_worker_bot_state_projects_human_ram_armor_results_exactly(self):
        worker = self._worker_client()
        worker._send_preencoded_trusted = self.client._send
        result = {
            'seq': 7, 'first_id': 1, 'second_id': 2,
            'available': True, 'armor_first': 45.0,
            'armor_second': 80.0,
        }

        self.assertTrue(worker.send_projected_bot_state(
            [], sample_time_us=40000,
            source_batch_horizon_us=40000,
            human_ram_armors=[result]))
        self.assertEqual([result], self.sent[-1]['human_ram_armors'])
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, armor_first=float('nan'))]))
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, unexpected=True)]))
        self.assertFalse(worker.send_projected_bot_state(
            [], human_ram_armors=[dict(result, first_id=2, second_id=1)]))

    def test_projectile_effect_carries_only_an_exact_stun_end_time(self):
        effect = {
            'target_kind': 'bot', 'target_id': 7,
            'damage': 0, 'shot_result': 2,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'stun_end_server_time_ms': 22000,
        }

        self.assertEqual(effect, _strict_projectile_effect(effect))
        self.assertIsNone(_strict_projectile_effect(dict(
            effect, stun_end_server_time_ms=True)))
        self.assertIsNone(_strict_projectile_effect(dict(
            effect, stun_duration_ms=7000)))

    def test_projectile_effect_target_pose_is_an_atomic_vector(self):
        effect = {
            'target_kind': 'bot', 'target_id': 7,
            'damage': 30, 'shot_result': 2,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'target_x': 4.0, 'target_y': 5.0, 'target_z': 6.0,
        }

        self.assertEqual(effect, _strict_projectile_effect(effect))
        incomplete = dict(effect)
        incomplete.pop('target_z')
        self.assertIsNone(_strict_projectile_effect(incomplete))

    def test_siege_request_is_an_exact_boolean_input_field(self):
        self.assertTrue(self.client.send_input(
            0.0, 0.0, siege_enabled=True))

        self.assertIs(True, self.sent[-1]['siege_enabled'])
        with self.assertRaisesRegex(ValueError, 'BOOL'):
            self.client.send_input(0.0, 0.0, siege_enabled=1)

    def test_input_carries_the_local_hull_pitch_and_roll(self):
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pitch=0.25, roll=-0.3))

        message = self.sent[-1]
        self.assertEqual(0.25, message['pitch'])
        self.assertEqual(-0.3, message['roll'])

    def test_track_repair_is_retired_for_the_rust_authority(self):
        before = list(self.sent)
        self.assertFalse(self.client.send_track_repair([{
            'name': 'leftTrackHealth', 'hp': 25.0,
            'max_hp': 100.0, 'state': 'destroyed',
        }], 4, 2))
        self.assertEqual(before, self.sent)
        self.assertFalse(self.client.send_track_repair([{
            'name': 'engineHealth', 'hp': 25.0,
            'max_hp': 100.0, 'state': 'destroyed',
        }], 4, 3))
        self.assertFalse(self.client.send_track_repair([{
            'name': 'leftTrackHealth', 'hp': 100.0,
            'max_hp': 100.0, 'state': 'normal',
        }], 4, 3))

    def test_timeline_input_has_round_sequence_and_server_pose_time(self):
        self.client.capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        self.client.server_capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)

        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pose_time_us=123456,
            ram_vx=1.25, ram_vy=-0.5, ram_vz=8.0))
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.5), yaw=0.4,
            pose_time_us=156789,
            ram_vx=1.5, ram_vy=-0.25, ram_vz=8.5))

        self.assertEqual((1, 123456), (
            self.sent[-2]['input_seq'], self.sent[-2]['pose_time_us']))
        self.assertEqual((2, 156789), (
            self.sent[-1]['input_seq'], self.sent[-1]['pose_time_us']))
        self.assertEqual((1.25, -0.5, 8.0), (
            self.sent[-2]['ram_vx'], self.sent[-2]['ram_vy'],
            self.sent[-2]['ram_vz']))

    def test_timeline_pose_requires_complete_bounded_native_ram_velocity(self):
        self.client.capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        self.client.server_capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        before = list(self.sent)

        for velocity in (
                {'ram_vx': 1.0, 'ram_vy': 2.0},
                {'ram_vx': 1.0, 'ram_vy': 2.0, 'ram_vz': True},
                {'ram_vx': 201.0, 'ram_vy': 2.0, 'ram_vz': 3.0},
                {'ram_vx': float('nan'), 'ram_vy': 2.0, 'ram_vz': 3.0}):
            self.assertFalse(self.client.send_input(
                0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
                pose_time_us=123456, **velocity))
        self.assertFalse(self.client.send_input(
            0.0, 0.0, ram_vx=1.0, ram_vy=2.0, ram_vz=3.0))
        self.assertEqual(before, self.sent)

    def test_player_pair_ram_receipts_use_a_separate_fact_only_ledger(self):
        self.client.capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        self.client.server_capabilities = (HUMAN_RAM_TIMELINE_CAPABILITY,)
        receipt = {
            'seq': 7, 'target_player_id': 2,
            'presentation_time_us': 123456,
        }

        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pose_time_us=123456, ram_vx=1.0, ram_vy=0.0, ram_vz=8.0,
            ram_contacts=[{'seq': 19, 'bot_id': 11}],
            player_ram_contacts=[receipt]))

        message = self.sent[-1]
        self.assertEqual([{'seq': 19, 'bot_id': 11}],
                         message['ram_contacts'])
        self.assertEqual([receipt], message['player_ram_contacts'])
        self.assertEqual(
            {'seq', 'target_player_id', 'presentation_time_us'},
            set(message['player_ram_contacts'][0]))
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pose_time_us=123456, ram_vx=1.0, ram_vy=0.0, ram_vz=8.0,
            player_ram_contacts=[receipt]))
        self.assertEqual([receipt], self.sent[-1]['player_ram_contacts'])

        before = list(self.sent)
        invalid_batches = (
            [dict(receipt, bot_id=11)],
            [dict(receipt, target_player_id=1)],
            [dict(receipt, seq=0)],
            [dict(receipt, seq=True)],
            [dict(receipt, presentation_time_us=1.5)],
            [dict(receipt, seq=8), dict(receipt, seq=8)],
            [dict(receipt, seq=8), dict(receipt, seq=10)],
        )
        for contacts in invalid_batches:
            self.assertFalse(self.client.send_input(
                0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
                pose_time_us=123456,
                ram_vx=1.0, ram_vy=0.0, ram_vz=8.0,
                player_ram_contacts=contacts))
        self.assertEqual(before, self.sent)

        self.client.player_id = 2
        self.assertFalse(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.4,
            pose_time_us=123456, ram_vx=1.0, ram_vy=0.0, ram_vz=8.0,
            player_ram_contacts=[dict(receipt, target_player_id=1)]))
        self.assertEqual(before, self.sent)

    def test_input_carries_bounded_ram_contact_ledger(self):
        contacts = [{'seq': value} for value in range(1, 20)]

        self.assertTrue(self.client.send_input(
            0.0, 0.0, ram_contacts=contacts))

        message = self.sent[-1]
        self.assertEqual(list(range(1, 17)), [
            value['seq'] for value in message['ram_contacts']])
        self.assertNotIn('ram_contact', message)

    def test_waiting_room_publishes_one_changed_garage_vehicle(self):
        self.client.phase = 'waiting'
        self.client.vehicle = 'ussr:R11_MS-1'
        self.client.max_health = 90

        self.assertFalse(self.client.select_vehicle('ussr:R11_MS-1', 90))
        self.assertTrue(self.client.select_vehicle(
            'germany:G01_PzI', 150,
            vehicle_compact_descr='cHpp'))

        self.assertEqual({'type': 'select_vehicle',
                          'vehicle': 'germany:G01_PzI',
                          'max_health': 150,
                          'vehicle_compact_descr': 'cHpp',
                          'effective_params': effective_params()},
                         self.sent[-1])
        # Only the server-published roster may retire the pending selection,
        # so a rejected update is resent on the next waiting roster.
        self.assertEqual('ussr:R11_MS-1', self.client.vehicle)

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 8,
            'state_revision': 9, 'phase': 'waiting', 'map': '01_karelia',
            'host_player_id': 1, 'authority_epoch': 0,
            'bot_authority_id': 0,
            'players': [dict(
                _player_equipment_contract(), id=1,
                vehicle='germany:G01_PzI', max_health=150,
                vehicle_compact_descr='cHpp',
                effective_params=effective_params())]})

        self.assertEqual('germany:G01_PzI', self.client.vehicle)
        self.assertEqual(150, self.client.max_health)
        self.assertFalse(self.client.select_vehicle('germany:G01_PzI', 150))

    def test_waiting_room_publishes_actor_scoped_authority_loadout(self):
        self.client.phase = 'waiting'
        loadout = {
            'repair': {'available': False},
            'spotting': {'available': False},
        }

        self.assertTrue(self.client.select_vehicle(
            'germany:G01_PzI', 150,
            player_authority_loadout=loadout))

        self.assertEqual(loadout,
                         self.sent[-1]['player_authority_loadout'])
        self.assertIn(
            lan_client_module.PLAYER_AUTHORITY_LOADOUT_CAPABILITY,
            lan_client_module.CLIENT_CAPABILITIES)

        connecting = LANClient(
            '127.0.0.1', 28782, 'P', 'germany:G01_PzI',
            vehicle_compact_descr='cHpp',
            effective_params=effective_params(),
            ammo_remaining=[30], ammo_loaded_shell=0,
            player_authority_loadout=loadout)
        self.assertEqual(
            loadout, connecting._hello_payload()[
                'player_authority_loadout'])

    def test_malformed_actor_authority_loadout_is_not_published(self):
        self.client.phase = 'waiting'

        self.assertFalse(self.client.select_vehicle(
            'germany:G01_PzI', 150,
            player_authority_loadout={
                'repair': {'available': False, 'borrowDonor': True},
                'spotting': {'available': False},
            }))
        self.assertEqual([], self.sent)


    def test_vehicle_selection_is_refused_outside_the_waiting_room(self):
        self.client.phase = 'battle'

        self.assertFalse(self.client.select_vehicle('germany:G01_PzI', 150))
        self.assertEqual([], self.sent)

    def test_host_can_set_each_team_size_during_the_waiting_room(self):
        from gui.mods.offline_lan_0922 import lan_client
        self.client.phase = 'waiting'
        self.client.server_capabilities = [
            lan_client.TEAM_SIZE_SELECTION_CAPABILITY]

        self.assertTrue(self.client.set_team_size(1, 4))
        self.assertTrue(self.client.set_team_size(2, 9))

        self.assertEqual({
            'type': 'set_team_size', 'team': 1, 'size': 4}, self.sent[-2])
        self.assertEqual({
            'type': 'set_team_size', 'team': 2, 'size': 9}, self.sent[-1])

    def test_team_size_request_requires_host_waiting_and_capability(self):
        from gui.mods.offline_lan_0922 import lan_client
        self.client.phase = 'waiting'
        self.client.server_capabilities = []
        self.assertFalse(self.client.set_team_size(1, 4))
        self.client.server_capabilities = [
            lan_client.TEAM_SIZE_SELECTION_CAPABILITY]
        self.client.host_player_id = 2
        self.assertFalse(self.client.set_team_size(1, 4))
        self.client.host_player_id = 1
        self.assertFalse(self.client.set_team_size(1, 0))
        self.assertFalse(self.client.set_team_size(3, 4))
        self.assertEqual([], self.sent)

    def test_team_size_denial_adopts_the_server_capacity(self):
        self.client.phase = 'waiting'
        events = []
        self.client.on_event = lambda kind, message: events.append(kind)

        self.client._handle_message({
            'type': 'team_size_denied', 'protocol': 5, 'round_id': 7,
            'state_revision': 5, 'team': 1, 'size': 2,
            'code': 'team_occupied',
            'team_sizes': {'1': 4, '2': 8},
        })

        self.assertEqual({1: 4, 2: 8}, self.client.team_sizes)
        self.assertEqual(['team_size_denied'], events)

    def test_descriptor_bundle_carries_full_terminal_contract(self):
        self.assertTrue(self.client.send_descriptor_bundle(
            {'test:good': {'name': 'test:good'}},
            requested=['test:good', 'test:bad'],
            failures=['test:bad'], complete=True))

        self.assertEqual({
            'type': 'descriptor_bundle', 'round_id': 7,
            'requested': ['test:good', 'test:bad'],
            'failures': ['test:bad'], 'complete': True,
            'projections': {'test:good': {'name': 'test:good'}},
        }, self.sent[-1])

    def test_destructible_map_is_round_fenced_and_chunked_deterministically(self):
        self.client.phase = 'loading'
        instances = [
            [[1] * 12, 10, 0, 25.0, None, 'a/resource'],
            [[2] * 12, 10, 1, 50.0, None, 'z/resource'],
        ]
        donation = {
            'unit_vehicle_mass': 15000.0,
            'resources': {
                'z/resource': {
                    'destr_type': 'tree', 'kinetic_correction': 0.5},
                'a/resource': {
                    'destr_type': 'fragile', 'kinetic_correction': 1.0},
            },
            'instances': instances,
        }

        with mock.patch.object(
                lan_client_module,
                'MAX_DESTRUCTIBLE_INSTANCES_PER_PART', 1), \
                mock.patch.object(
                    lan_client_module,
                    'MAX_DESTRUCTIBLE_RESOURCES_PER_PART', 1):
            self.assertTrue(self.client.send_destructible_map(
                '01_karelia', donation))

        self.assertEqual(2, len(self.sent))
        self.assertEqual({
            'type': 'destructible_map', 'round_id': 7,
            'map': '01_karelia', 'part': 0, 'parts': 2,
            'unit_vehicle_mass': 15000.0,
            'resources': {
                'a/resource': {
                    'destr_type': 'fragile', 'kinetic_correction': 1.0}},
            'instances': [instances[0]],
        }, self.sent[0])
        self.assertEqual({
            'type': 'destructible_map', 'round_id': 7,
            'map': '01_karelia', 'part': 1, 'parts': 2,
            'unit_vehicle_mass': 15000.0,
            'resources': {
                'z/resource': {
                    'destr_type': 'tree', 'kinetic_correction': 0.5}},
            'instances': [instances[1]],
        }, self.sent[1])

    def test_destructible_map_requires_loading_and_complete_outer_shape(self):
        donation = {
            'unit_vehicle_mass': 15000.0,
            'resources': {'a/resource': {
                'destr_type': 'tree', 'kinetic_correction': 0.5}},
            'instances': [[[1] * 12, 10, 0, 25.0, None, 'a/resource']],
        }
        self.assertFalse(self.client.send_destructible_map(
            '01_karelia', donation))
        self.client.phase = 'loading'
        self.assertFalse(self.client.send_destructible_map(
            '01_karelia', dict(donation, unexpected=True)))
        self.assertFalse(self.client.send_destructible_map(
            '01_karelia', dict(donation, instances=[])))
        self.assertEqual([], self.sent)

    def test_bot_speed_survives_projection_and_snapshot_sync(self):
        source = {
            'id': 3, 'x': 1.0, 'y': 0.0, 'z': 2.0, 'yaw': 0.1,
            'pitch': 0.0, 'roll': 0.0, 'aim_yaw': 0.1,
            'gun_pitch': 0.0, 'speed': 6.25,
            'movement_dir': 1, 'rotation_dir': 0,
            'fire_seq': 0, 'health': 500, 'alive': True,
            'reload_time': 0.25, 'reload_duration': 0.5,
        }
        projected = project_bot_state(source)
        self.assertEqual(6.25, projected['speed'])

        events = SnapshotSync(
            local_player_id=1, clock=lambda: 10.0).snapshot({
                'round_id': 7, 'server_tick': 1,
                'bot_state_revision': 1,
                'motion_time_us': 100000,
                'bot_state_time_us': 100000,
                'players': [], 'bots': [projected],
            })
        update = next(event for event in events
                      if event['type'] == 'update')
        self.assertEqual(6.25, update['state']['speed'])


    def test_critical_hit_requires_exact_target_contract(self):
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        self.assertFalse(
            self.client.send_hit(2, 1, 100, 2, critical=critical))
        worker = self._worker_client()
        with self.assertRaises(ValueError):
            worker.send_bot_hit(
                2, 1, 100, 2, critical=critical,
                critical_target_base_revision=True,
                critical_target_ack_seq=0, hull_damage=100)
        with self.assertRaises(ValueError):
            worker.send_bot_human_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0.5, hull_damage=100)
        with self.assertRaises(ValueError):
            worker.send_bot_bot_hit(
                1, 2, 1, 100, 2, critical=critical,
                critical_target_base_revision=0,
                critical_target_ack_seq=0, hull_damage=-1)
        self.assertEqual([], self.sent)


    def test_destructible_report_requires_exact_identity_fields(self):
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1.5,
            'item_index': 2}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'unknown', 'chunk_id': 1,
            'item_index': 2}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1,
            'item_index': 2, 'x': 0, 'y': 0, 'z': 0,
            'fall_yaw': 0, 'speed': 0}))
        self.assertFalse(self.client.send_destructible({
            'destructible_kind': 'tree', 'chunk_id': 1,
            'item_index': 2, 'x': 0, 'y': 0, 'z': 0,
            'fall_yaw': 0, 'speed': 0, 'is_shot': 0}))

    def test_input_has_no_client_damage_verdict_fields(self):
        self.assertTrue(self.client.send_input(0, 0, speed=-12.5))

        message = self.sent[-1]
        self.assertFalse(any(
            key.startswith('reported_') for key in message))
        self.assertEqual(-12.5, message['speed'])
        with self.assertRaises(TypeError):
            self.client.send_input(0, 0, reported_health=0)

    def test_visible_client_cannot_send_bot_or_rule_messages(self):
        self.client.bot_authority_id = 0
        self.assertFalse(self.client.send_bot_manifest([{'id': 1}]))
        self.assertFalse(self.client.send_bot_state([{'id': 1}]))
        self.assertFalse(self.client.send_bot_observation([{}]))
        self.assertFalse(self.client.send_bot_human_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_bot_hit(1, 2, 1, 100, 2))
        self.assertFalse(self.client.send_bot_ram(
            1, 'human', 2, 1, 20, 80))
        self.assertFalse(self.client.send_rules_state({}))
        self.assertFalse(self.client.send_battle_result(1, 'elimination'))
        self.assertFalse(self.client.send_bot_hit(1, 1, 100, 2))
        self.assertEqual([], self.sent)

    def test_failed_fire_send_does_not_create_sequence_gap(self):
        launch = {
            'position': [1.0, 2.0, 3.0],
            'velocity': [900.0, 0.0, 0.0],
            'gravity': 9.81,
            'max_distance': 720.0,
            'max_time_ms': 20000,
            'source_shot': {
                'speed': 900.0, 'gravity': 9.81,
                'maxDistance': 720.0,
                'piercingPower': [220.0, 200.0],
                'deadeye': False,
                'shell': {
                    'kind': 'ARMOR_PIERCING', 'caliber': 105.0,
                    'damage': [390.0, 150.0],
                    'explosionRadius': 0.0,
                },
            },
        }
        self.assertTrue(self.client.send_input(
            0.0, 0.0, position=(1.0, 2.0, 3.0), yaw=0.0,
            shell_index=0, pose_time_us=123456))
        self.client._send = lambda unused_message: False

        self.assertIsNone(self.client.send_fire(**launch))
        self.assertEqual(0, self.client._fire_intent_seq)

        self.client._send = lambda message: self.sent.append(message) or True
        self.assertEqual(1, self.client.send_fire(**launch))
        self.assertEqual({
            'type': 'fire_intent', 'round_id': 7,
            'intent_seq': 1, 'input_seq': 1, 'shell_index': 0,
            'shot_origin': [1.0, 2.0, 3.0],
            'shot_direction': [1.0, 0.0, 0.0],
            'dispersion_angle': 0.0,
        }, self.sent[-1])
        self.assertFalse(any(field in self.sent[-1]
                             for field in ('position', 'origin', 'velocity',
                                           'gravity', 'source_shot',
                                           'damage')))

    def test_worker_sends_hello_before_exposing_connected_socket(self):
        client = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1',
            effective_params=effective_params(),
            ammo_remaining=[30], ammo_loaded_shell=0,
            player_authority_loadout={
                'repair': {'available': False},
                'spotting': {'available': False},
            })
        sent = []
        outer = self

        class FakeSocket(object):
            def settimeout(self, unused_timeout):
                pass

            def connect(self, unused_address):
                pass

            def setsockopt(self, *unused_args):
                pass

            def sendall(self, payload):
                outer.assertFalse(client.connected)
                sent.append(json.loads(payload.decode('utf-8')))

            def recv(self, unused_size):
                return b''

            def close(self):
                pass

        import gui.mods.offline_lan_0922.lan_client as lan_client_module
        original_socket = lan_client_module.socket.socket
        lan_client_module.socket.socket = lambda *unused_args: FakeSocket()
        client.running = True
        try:
            client._worker()
        finally:
            lan_client_module.socket.socket = original_socket

        self.assertEqual('hello', sent[0]['type'])
        self.assertEqual(5, sent[0]['protocol'])
        self.assertEqual('wot-0.9.22.0.1-cn-1513',
                         sent[0]['client_build'])

    def test_pong_uses_network_receive_time_before_main_thread_delay(self):
        self.client.rtt_ms = None
        self.client._handle_message({
            'type': 'pong', 'client_time': 10.0,
            '_client_received_time': 10.025,
        })

        self.assertAlmostEqual(25.0, self.client.rtt_ms, places=3)

    def test_bot_observation_is_validated_and_stale_round_is_ignored(self):
        received = []
        self.client.on_event = (
            lambda kind, message: received.append((kind, message)))
        message = {
            'type': 'bot_observation', 'protocol': 5, 'round_id': 7,
            'contacts': [{
                'observing_team': 2, 'target_kind': 'human',
                'target_id': 1, 'target_team': 1, 'visible': True,
                'fresh': True, 'time_left': 10.0,
                'visible_by_bot_ids': [11],
                'visible_by_player_ids': [],
                'shootable_by_bot_ids': [],
            }],
        }

        self.client._handle_message(message)
        self.assertEqual(['bot_observation'],
                         [kind for kind, unused in received])

        stale = dict(message, round_id=6)
        self.client._handle_message(stale)
        self.assertEqual(1, len(received))
        self.client._handle_message(dict(stale, protocol='obsolete'))
        self.assertTrue(self.client.ready)
        self.assertIsNone(self.client.last_error)

        malformed = dict(message)
        malformed['contacts'] = [dict(
            message['contacts'][0], visible=1)]
        self.client.running = True
        self.client._handle_message(malformed)
        self.assertTrue(self.client.running)
        self.assertTrue(self.client.ready)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(1, len(received))

    def test_server_timing_projects_receive_time_and_half_rtt(self):
        self.client.rtt_ms = 100.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertAlmostEqual(114.95, self.client.combat_deadline, places=3)
        self.assertAlmostEqual(
            1014.95, self.client.combat_end_deadline, places=3)
        self.assertEqual('prebattle', self.client.combat_phase)

    def test_server_timing_rejects_inconsistent_payload(self):
        self.assertFalse(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900001, 'duration_ms': 900000}}))

    def test_invalid_snapshot_timing_keeps_state_and_old_clock(self):
        self.client.running = True
        message = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'bot_authority_id': 0, 'bot_manifest': [],
            'players': [_snapshot_player()], 'bots': [],
            'timing': {
                'phase': 'battle', 'start_in_ms': 0,
                'remaining_ms': 900001, 'duration_ms': 900000},
        }

        self.client._handle_message(message)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(1, self.client.last_snapshot['server_tick'])
        self.assertNotIn('timing', self.client.last_snapshot)
        self.assertEqual(-1, self.client._combat_timing_tick)

    def test_future_runtime_round_still_fails_lineage(self):
        self.client.running = True
        self.client._handle_message({
            'type': 'events', 'protocol': 5, 'round_id': 8,
            'server_tick': 1, 'events': [],
        })

        self.assertFalse(self.client.running)
        self.assertEqual('invalid events message', self.client.last_error)

    def test_current_runtime_message_without_protocol_is_recoverable(self):
        self.client.running = True
        message = {
            'type': 'snapshot', 'round_id': 7, 'server_tick': 1,
            'players': [], 'bots': [],
        }
        self.client._handle_message(message)
        self.client._handle_message(message)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIsNone(self.client.last_snapshot)
        self.assertEqual(
            1, self.client._runtime_drop_diagnostics['snapshot'][1])

    def test_negotiated_runtime_protocol_label_is_informational(self):
        self.client.running = True
        self.client._schema_negotiated = True
        self.client._handle_message({
            'type': 'events', 'protocol': 4, 'round_id': 7,
            'server_tick': 1, 'events': [],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)

    def test_invalid_runtime_protocol_marker_soft_drops_one_message(self):
        self.client.running = True
        self.client._schema_negotiated = True
        self.client._handle_message({
            'type': 'events', 'protocol': 'invalid', 'round_id': 7,
            'server_tick': 1, 'events': [],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIn('events', self.client._runtime_drop_diagnostics)

    def test_snapshot_missing_bot_combat_contract_keeps_last_good_state(self):
        self.client.running = True
        self.client._handle_message({
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1,
            'players': [{
                'id': 1, 'critical_revision': 0,
                'critical_base_revision': 0, 'critical_ack_seq': 0}],
            'bots': [{'id': 11, 'health': 500, 'alive': True}],
        })

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual('battle', self.client.phase)
        self.assertIsNone(self.client.last_snapshot)

    def test_lean_snapshot_inherits_the_last_static_bot_manifest(self):
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        player = _snapshot_player()
        manifest = [{'id': 11, 'vehicle': 'ussr:R11_MS-1'}]
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': manifest,
            'bot_authority_id': 0,
        }
        second = dict(first, server_tick=2)
        second.pop('bot_manifest')

        self.client._handle_message(first)
        self.client._handle_message(second)

        self.assertEqual(manifest, self.client.last_snapshot['bot_manifest'])
        self.assertIsNot(
            manifest, self.client.last_snapshot['bot_manifest'])

    def test_poll_does_not_coalesce_away_manifest_barrier(self):
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        self.client.running = True
        self.client.connected = False
        self.client.bigworld = mock.Mock()
        self.client.bigworld.callback.return_value = 1
        player = _snapshot_player()
        manifest = [{'id': 11, 'vehicle': 'ussr:R11_MS-1'}]
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': manifest,
            'bot_authority_id': 0, 'authority_epoch': 1,
        }
        second = dict(first, server_tick=3)
        second.pop('bot_manifest')
        with self.client._pending_lock:
            self.client._pending = [first, second]

        self.client._poll()

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertEqual(3, self.client.last_snapshot['server_tick'])
        self.assertEqual(manifest, self.client.last_snapshot['bot_manifest'])

    def test_lean_snapshot_requires_server_capability(self):
        self.client.running = True
        player = _snapshot_player()
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': [],
            'bot_authority_id': 0, 'authority_epoch': 1,
        }
        second = dict(first, server_tick=2)
        second.pop('bot_manifest')

        self.client._handle_message(first)
        accepted = self.client.last_snapshot
        self.client._handle_message(second)

        self.assertTrue(self.client.running)
        self.assertIsNone(self.client.last_error)
        self.assertIs(accepted, self.client.last_snapshot)

    def test_lean_snapshot_requires_existing_same_lineage_manifest(self):
        self.client.running = True
        player = _snapshot_player()
        self.client.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        first = {
            'type': 'snapshot', 'protocol': 5, 'round_id': 7,
            'server_tick': 1, 'bot_state_revision': 0,
            'players': [player], 'bots': [], 'bot_manifest': [],
            'bot_authority_id': 0, 'authority_epoch': 1,
        }
        changed = dict(
            first, server_tick=2, bot_authority_id=0,
            authority_epoch=2)
        changed.pop('bot_manifest')

        self.client._handle_message(first)
        self.client._handle_message(changed)

        self.assertEqual('invalid snapshot message', self.client.last_error)
        self.assertEqual('disconnected', self.client.phase)

        missing = LANClient(
            '127.0.0.1', 28782, 'P', 'ussr:MS-1')
        missing.running = True
        missing.ready = True
        missing.phase = 'battle'
        missing.round_id = 7
        missing.server_capabilities = (
            LEAN_SNAPSHOT_MANIFEST_CAPABILITY,)
        first.pop('bot_manifest')
        missing._handle_message(first)
        self.assertTrue(missing.running)
        self.assertIsNone(missing.last_error)
        self.assertIsNone(missing.last_snapshot)

    def test_snapshot_drops_non_exact_bot_combat_revisions(self):
        cases = (
            ('combat_revision', True),
            ('combat_base_revision', True),
            ('combat_ack_seq', True),
            ('combat_base_revision', 2),
            ('combat_ack_seq', -1),
            ('combat_fire_elapsed', float('nan')),
            ('combat_fire_elapsed', -0.1),
            ('combat_fire_elapsed', 10.1),
            ('combat_fire_timer', True),
            ('combat_fire_timer', 1.0),
        )
        for field, value in cases:
            client = LANClient(
                '127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': {},
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            bot[field] = value

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0, 'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertTrue(client.running, '%s=%r' % (field, value))
            self.assertIsNone(client.last_error, '%s=%r' % (field, value))
            self.assertEqual('battle', client.phase)
            self.assertIsNone(client.last_snapshot)

    def test_snapshot_drops_missing_or_non_object_bot_critical(self):
        for critical in (None, [], 'broken'):
            client = LANClient('127.0.0.1', 28782, 'P', 'ussr:MS-1')
            client.running = True
            client.ready = True
            client.phase = 'battle'
            client.round_id = 7
            client._send = lambda unused: True
            bot = {
                'id': 11, 'health': 500, 'alive': True,
                'critical': critical,
                'combat_revision': 1, 'combat_base_revision': 1,
                'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0,
                'combat_fire_timer': 0.0,
            }
            if critical is None:
                bot.pop('critical')

            client._handle_message({
                'type': 'snapshot', 'protocol': 5, 'round_id': 7,
                'server_tick': 1,
                'players': [{
                    'id': 1, 'critical_revision': 0,
                    'critical_base_revision': 0,
                    'critical_ack_seq': 0}],
                'bots': [bot],
            })

            self.assertTrue(client.running)
            self.assertIsNone(client.last_error)
            self.assertEqual('battle', client.phase)
            self.assertIsNone(client.last_snapshot)

    def test_loading_ready_and_battle_live_form_one_transition(self):
        self.client.phase = 'loading'
        bases = {'1': [(-10.0, -20.0)], '2': [(10.0, 20.0)]}
        self.assertTrue(self.client.send_battle_ready(bases))
        self.assertEqual('battle_ready', self.sent[-1]['type'])
        self.assertEqual(bases, self.sent[-1]['bases'])

        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0,
            'bot_authority_id': 0,
            'state_revision': 5, 'countdown_seconds': 30.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 30000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.assertEqual('battle', self.client.phase)

    def test_older_timing_cannot_rewind_a_newer_snapshot_deadline(self):
        self.client.phase = 'loading'
        self.client.rtt_ms = 0.0
        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 1,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 14967,
                'remaining_ms': 900000, 'duration_ms': 900000}}))
        deadline = self.client.combat_deadline

        self.assertTrue(self.client._load_server_timing({
            'round_id': 7, 'server_tick': 0,
            '_client_received_time': 100.5,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}))

        self.assertEqual(deadline, self.client.combat_deadline)
        self.assertEqual(1, self.client._combat_timing_tick)

    def test_newer_roster_revision_cannot_swallow_first_battle_live(self):
        events = []
        self.client.on_event = lambda kind, message: events.append(kind)
        self.client.phase = 'loading'
        self.client.state_revision = 5
        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': 0,
            'players': [dict(
                _player_equipment_contract(), id=1,
                effective_params=effective_params())]})
        live = {
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'bot_authority_id': 0,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            '_client_received_time': 100.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}}

        self.client._handle_message(live)
        self.client._handle_message(live)

        self.assertEqual('battle', self.client.phase)
        self.assertEqual(7, self.client.state_revision)
        self.assertEqual(7, self.client._battle_live_round_id)
        self.assertAlmostEqual(115.0, self.client.combat_deadline)
        self.assertEqual(['roster', 'battle_live'], events)

    def test_visible_client_rejects_player_authority(self):
        self.client.player_id = 2
        self.client.phase = 'waiting'
        players = [
            {'id': 1, 'team': 1, 'slot': 0, 'name': 'Failed',
             'vehicle': 'ussr:MS-1', 'x': 0, 'y': 0, 'z': 0},
            {'id': 2, 'team': 2, 'slot': 0, 'name': 'Survivor',
             'vehicle': 'ussr:MS-1', 'x': 1, 'y': 0, 'z': 1},
        ]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': 1, 'players': players})
        self.assertEqual('waiting', self.client.phase)
        self.assertEqual('invalid battle_start message',
                         self.client.last_error)
        self.assertFalse(self.client.is_bot_authority())

    def test_visible_client_accepts_only_explicit_worker_failure_without_id(self):
        self.client.authority_epoch = 1

        self.client._handle_message({
            'type': 'events', 'protocol': 5, 'round_id': 7,
            'server_tick': 8, 'authority_epoch': 2,
            'bot_authority_id': None,
            'worker_status': 'failed',
            'worker_failure_reason': 'worker_disconnected',
            'events': [{
                'kind': 'authority', 'player_id': None,
                'authority_epoch': 2,
            }],
        })

        self.assertIsNone(self.client.last_error)
        self.assertIsNone(self.client.bot_authority_id)
        self.assertEqual(2, self.client.authority_epoch)
        self.assertFalse(self.client.is_bot_authority())

    def test_same_round_waiting_roster_cannot_demote_accepted_battle(self):
        players = [dict(
            _player_equipment_contract(), id=1, team=1, slot=0, name='P',
            vehicle='ussr:MS-1', x=0, y=0, z=0,
            effective_params=effective_params())]
        self.client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 7,
            'state_revision': 5,
            'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 1,
            'bot_authority_id': 0,
            'players': players})
        self.client._handle_message({
            'type': 'battle_live', 'protocol': 5, 'round_id': 7,
            'server_tick': 0, 'state_revision': 6,
            'bot_authority_id': 0,
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0,
            'timing': {
                'phase': 'prebattle', 'start_in_ms': 15000,
                'remaining_ms': 900000, 'duration_ms': 900000}})

        self.client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 7,
            'state_revision': 7,
            'phase': 'waiting', 'map': '05_prohorovka',
            'bot_authority_id': 0,
            'host_player_id': 1, 'players': [dict(
                _player_equipment_contract(), id=1, team=1, slot=0,
                name='Changed', vehicle='ussr:MS-1', x=1, y=0, z=1,
                effective_params=effective_params())]})

        self.assertEqual('battle', self.client.phase)
        self.assertEqual('01_karelia', self.client.map_name)
        self.assertEqual(players, self.client.roster)

if __name__ == '__main__':
    unittest.main()


class ClientEventVocabularyTests(unittest.TestCase):
    """Keep the client-side ordered event dispatcher explicit."""

    CLIENT = (Path(__file__).resolve().parents[1] / 'src' / 'res' /
              'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922' /
              'battle_runtime.py')

    def _client_kinds(self):
        namespace = {}
        source = self.CLIENT.read_text()
        for name in ('_SHOT_EVENT_KINDS', '_COMBAT_EVENT_KINDS',
                     '_SIMPLE_EVENT_KINDS'):
            match = re.search(
                r'^%s = \(.*?\)' % name, source, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(match, name)
            exec(compile(match.group(0), name, 'exec'), namespace)
        return (set(namespace['_SHOT_EVENT_KINDS']) |
                set(namespace['_COMBAT_EVENT_KINDS']) |
                set(namespace['_SIMPLE_EVENT_KINDS']))

    def test_battle_result_stays_in_the_client_vocabulary(self):
        self.assertIn('battle_result', self._client_kinds())
