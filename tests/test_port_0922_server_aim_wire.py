"""The projectile owner uses native aim locally; no server marker is relayed."""

import copy
import unittest
from unittest import mock

import test_port_0922_lan_client_projectiles as client_fixtures
from test_port_0922_server_projectiles import (
    _gun_checkpoint, _launch, _launch_authority, _state, _update_player_input)
from gui.mods.offline_lan_0922 import lan_client
from lan_battle_server import SIMULATION_WORKER_AUTHORITY_ID


class LocalAimWireTests(unittest.TestCase):
    def test_player_input_and_public_state_have_no_marker_checkpoint(self):
        state = _state()
        self.assertTrue(_update_player_input(state, 1))
        player = state.players[1]
        public = state._public_player(player)
        self.assertTrue(lan_client._valid_player_gun_checkpoint_contract(public))
        self.assertEqual(_gun_checkpoint(), public['gun_checkpoint'])
        for key in ('gun_aim_checkpoint', 'gun_aim_checkpoint_seq', 'gun_marker'):
            self.assertNotIn(key, public)
            self.assertFalse(hasattr(player, key))

    def test_retired_marker_input_is_local_rejection_and_next_frame_recovers(self):
        state = _state()
        self.assertTrue(_update_player_input(state, 1))
        player = state.players[1]
        self.assertFalse(_update_player_input(
            state, 1, gun_aim_checkpoint={'position': [0, 0, 0]}, forward=1.0))
        self.assertEqual((1, 2), (player.input_seq, player.input_processed_seq))
        self.assertEqual(0.0, player.forward)
        self.assertTrue(_update_player_input(state, 1, forward=0.5))
        self.assertEqual(3, player.input_seq)
        self.assertEqual(0.5, player.forward)

    def test_worker_marker_payload_cannot_repopulate_public_player_state(self):
        state = _state()
        state.bot_roster = []
        state.bot_manifest = []
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.update_bot_states(SIMULATION_WORKER_AUTHORITY_ID, {
            'type': 'bot_state', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch, 'rows': [],
            'player_gun_markers': [{'player_id': 1, 'input_seq': 1,
                                    'origin': [1, 2, 3]}],
        }))
        self.assertNotIn('gun_marker', state._public_player(state.players[1]))
        self.assertIsNone(state.battle_result)

    def test_owner_launch_freezes_geometry_without_native_aim_checkpoint(self):
        state = _state()
        self.assertTrue(_update_player_input(state, 1))
        launch = _launch(origin=[0.5, 2.0, 3.0])
        self.assertTrue(_launch_authority(state, launch))
        record = state.projectiles['1:p:1:1']
        frozen = copy.deepcopy(record)
        self.assertTrue(_update_player_input(state, 1, aim_yaw=0.8))
        launch['origin'][0] = 123.0
        self.assertEqual(frozen, record)
        self.assertEqual(1, record['fire_input_seq'])
        self.assertEqual([0.5, 2.0, 3.0], record['origin'])

    def test_client_input_keeps_gun_state_without_sampling_or_sending_marker(self):
        client = client_fixtures.ProjectileWireTests().active_client()
        sent = []
        with mock.patch.object(client, '_send', side_effect=lambda value:
                               sent.append(value) or True):
            self.assertTrue(client.send_input(
                0.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0,
                pose_time_us=10, shell_index=0, next_shell_index=0,
                shell_change_pending=False, gun_checkpoint=_gun_checkpoint()))
        self.assertEqual(1, sent[0]['input_seq'])
        self.assertEqual(_gun_checkpoint(), sent[0]['gun_checkpoint'])
        self.assertNotIn('gun_aim_checkpoint', sent[0])
        self.assertNotIn('gun_aim_checkpoint_seq', sent[0])
        self.assertNotIn('gun_marker', sent[0])

    def test_client_no_longer_accepts_marker_checkpoint_argument(self):
        client = client_fixtures.ProjectileWireTests().active_client()
        with self.assertRaises(TypeError):
            client.send_input(0.0, 0.0, gun_aim_checkpoint={})
        self.assertEqual(0, client._input_seq)

    def test_worker_state_batch_has_no_player_marker_payload(self):
        client = client_fixtures.ProjectileWireTests().active_worker_client()
        self.assertTrue(client.send_projected_bot_state(
            [], edge_sample_time_us=0, edge_revision=1))
        queued = client._dequeue_outbound(client._transport_generation)
        payload = client_fixtures.wire_copy(queued[1])
        self.assertEqual('bot_state', payload['type'])
        self.assertNotIn('player_gun_markers', payload)
        with self.assertRaises(TypeError):
            client.send_projected_bot_state(
                [], edge_sample_time_us=0, edge_revision=1,
                player_gun_markers=[])
