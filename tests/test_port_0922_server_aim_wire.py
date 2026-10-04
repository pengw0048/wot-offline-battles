import copy
import unittest
from unittest import mock

import test_port_0922_lan_client_projectiles as client_fixtures
from test_port_0922_server_projectiles import (
    _fire_intent, _gun_checkpoint, _state, _update_player_input)
import lan_battle_server as server_module
from gui.mods.offline_lan_0922 import lan_client
from lan_battle_server import (
    MAX_PLAYER_INPUT_FINGERPRINTS, SIMULATION_WORKER_AUTHORITY_ID)


def checkpoint(**changes):
    value = {'position': [0.0, 0.3, 0.0], 'rotation': [0.0, 0.1, -0.2],
             'turret_yaw': 0.4, 'gun_pitch': -0.05,
             'dispersion_angle': 0.012}
    value.update(changes)
    return value


def marker(sequence=1, **changes):
    value = {'input_seq': sequence, 'origin': [0.5, 2.0, 3.0],
             'direction': [0.0, 0.0, 1.0], 'dispersion_angle': 0.012,
             'shot_speed': 800.0}
    value.update(changes)
    return value


class ServerAimWireTests(unittest.TestCase):
    def setUp(self):
        self.state = _state()
        self.state.bot_roster = []
        self.state.bot_manifest = []
        self.state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID

    def publish(self, rows, **changes):
        frame = {'type': 'bot_state', 'round_id': self.state.round_id,
                 'authority_epoch': self.state.authority_epoch,
                 'rows': [], 'player_gun_markers': rows}
        frame.update(changes)
        return self.state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, frame)

    def admit(self, value=None):
        return _update_player_input(
            self.state, 1,
            gun_aim_checkpoint=checkpoint() if value is None else value)

    def test_checkpoint_and_marker_round_trip_through_public_player(self):
        source = checkpoint()
        self.assertTrue(self.admit(source))
        source['position'][0] = 100.0
        player = self.state.players[1]
        self.assertEqual(checkpoint(), player.gun_aim_checkpoint)
        self.assertEqual(1, player.gun_aim_checkpoint_seq)
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        public = self.state._public_player(player)
        self.assertTrue(lan_client._valid_player_gun_checkpoint_contract(public))
        self.assertEqual(marker(), public['gun_marker'])
        public['gun_marker']['origin'][0] = 99.0
        public['gun_aim_checkpoint']['position'][0] = 99.0
        self.assertEqual(marker(), player.gun_marker)
        self.assertEqual(checkpoint(), player.gun_aim_checkpoint)

    def test_bad_checkpoint_is_rejected_without_advancing_applied_state(self):
        self.assertTrue(self.admit())
        player = self.state.players[1]
        for value in (None, {}, checkpoint(turret_yaw=True),
                      checkpoint(dispersion_angle=float('nan')),
                      checkpoint(position=[100.0, 0.0, 0.0])):
            with self.subTest(value=value):
                applied_seq = player.input_seq
                previous = copy.deepcopy(player.gun_aim_checkpoint)
                self.assertFalse(_update_player_input(
                    self.state, 1, gun_aim_checkpoint=value, forward=1.0))
                self.assertEqual(applied_seq, player.input_seq)
                self.assertEqual(previous, player.gun_aim_checkpoint)
                self.assertEqual(0.0, player.forward)
                self.assertGreater(player.input_processed_seq, applied_seq)

    def test_fire_relay_deeply_freezes_the_matching_aim_input(self):
        self.assertTrue(self.admit())
        player = self.state.players[1]
        self.assertTrue(self.state.submit_fire_intent(
            1, _fire_intent(self.state)))
        relay = player.pending_fire_intents[1]
        self.assertEqual(1, relay['gun_aim_checkpoint_seq'])
        self.assertEqual(checkpoint(), relay['gun_aim_checkpoint'])
        self.assertTrue(self.admit(checkpoint(turret_yaw=0.8)))
        player.gun_aim_checkpoint['position'][0] = 123.0
        self.assertEqual(checkpoint(), relay['gun_aim_checkpoint'])
        self.assertEqual(1, relay['input_seq'])
        self.assertEqual([], relay['presentation_ledger'])

    def test_missing_checkpoint_preserves_old_fire_but_retires_old_markers(self):
        self.assertTrue(self.admit())
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertTrue(_update_player_input(self.state, 1))
        player = self.state.players[1]
        self.assertEqual({}, player.gun_marker)
        self.assertEqual({}, player.gun_aim_checkpoint)
        self.assertFalse(player.gun_aim_checkpoints)
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertEqual({}, player.gun_marker)
        self.assertTrue(self.state.submit_fire_intent(
            1, _fire_intent(self.state)))
        relay = player.pending_fire_intents[1]
        self.assertNotIn('gun_aim_checkpoint', relay)
        self.assertIn('shot_direction', relay)

    def test_worker_may_trail_input_frontier_but_cannot_rewind_a_marker(self):
        for unused in range(4):
            self.assertTrue(self.admit())
        self.assertTrue(self.publish([dict(marker(2), player_id=1)]))
        self.assertEqual(marker(2), self.state.players[1].gun_marker)
        for bad in (marker(1), marker(2, shot_speed=200.0), marker(5)):
            self.assertTrue(self.publish([dict(bad, player_id=1)]))
            self.assertEqual(marker(2), self.state.players[1].gun_marker)
        self.assertTrue(self.publish([dict(marker(4), player_id=1)]))
        self.assertEqual(marker(4), self.state.players[1].gun_marker)

    def test_marker_metadata_never_changes_fire_ammunition_or_checkpoint(self):
        self.assertTrue(self.admit())
        player = self.state.players[1]
        before = (player.fire_seq, player.fire_intent_seq,
                  copy.deepcopy(player.gun_checkpoint),
                  dict(player.gun_checkpoints))
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertEqual(before, (player.fire_seq, player.fire_intent_seq,
                                 player.gun_checkpoint,
                                 dict(player.gun_checkpoints)))
        self.assertFalse(player.pending_fire_intents)

    def test_invalid_marker_is_local_and_does_not_block_valid_peer_or_bot_tick(self):
        self.assertTrue(self.admit())
        self.assertTrue(_update_player_input(
            self.state, 2, gun_aim_checkpoint=checkpoint(position=[10, 0, 0])))
        revision = self.state.bot_state_revision
        self.assertTrue(self.publish([
            dict(marker(direction=[0, 0, 0]), player_id=1),
            dict(marker(origin=[10, 2, 3]), player_id=2)]))
        self.assertEqual({}, self.state.players[1].gun_marker)
        self.assertEqual(marker(origin=[10, 2, 3]),
                         self.state.players[2].gun_marker)
        self.assertGreater(self.state.bot_state_revision, revision)

    def test_unaccepted_future_distant_and_dead_player_markers_are_ignored(self):
        self.assertTrue(self.admit())
        for bad in (dict(marker(), player_id=999),
                    dict(marker(2), player_id=1),
                    dict(marker(dispersion_angle=0.5), player_id=1),
                    dict(marker(origin=[100, 0, 0]), player_id=1)):
            self.assertTrue(self.publish([bad]))
            self.assertEqual({}, self.state.players[1].gun_marker)
        self.state.players[1].alive = False
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertEqual({}, self.state.players[1].gun_marker)

    def test_wrong_round_epoch_or_authority_cannot_publish_a_marker(self):
        self.assertTrue(self.admit())
        self.assertFalse(self.publish(
            [dict(marker(), player_id=1)], round_id=self.state.round_id + 1))
        self.assertTrue(self.publish(
            [dict(marker(), player_id=1)],
            authority_epoch=self.state.authority_epoch - 1))
        self.assertFalse(self.state.update_bot_states(1, {
            'type': 'bot_state', 'round_id': self.state.round_id,
            'authority_epoch': self.state.authority_epoch,
            'rows': [], 'player_gun_markers': [dict(marker(), player_id=1)]}))
        self.assertEqual({}, self.state.players[1].gun_marker)

    def test_aim_history_is_bounded_and_expired_response_is_ignored(self):
        for unused in range(MAX_PLAYER_INPUT_FINGERPRINTS + 1):
            self.assertTrue(self.admit())
        player = self.state.players[1]
        self.assertEqual(MAX_PLAYER_INPUT_FINGERPRINTS,
                         len(player.gun_aim_checkpoints))
        self.assertNotIn(1, player.gun_aim_checkpoints)
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.assertEqual({}, player.gun_marker)

    def test_authority_change_and_round_reset_retire_marker_state(self):
        self.assertTrue(self.admit())
        self.assertTrue(self.publish([dict(marker(), player_id=1)]))
        self.state.simulation_worker.connected = False
        self.state._elect_bot_authority()
        player = self.state.players[1]
        self.assertEqual({}, player.gun_marker)
        self.state._reset_round()
        self.assertEqual({}, player.gun_aim_checkpoint)
        self.assertEqual(0, player.gun_aim_checkpoint_seq)
        self.assertFalse(player.gun_aim_checkpoints)

    def test_marker_expires_without_new_worker_sample_and_retry_cannot_refresh_it(self):
        self.assertTrue(self.admit())
        with mock.patch.object(server_module.time, 'monotonic', return_value=100.0):
            self.assertTrue(self.publish([dict(marker(), player_id=1)]))
            self.assertIn('gun_marker', self.state._public_player(
                self.state.players[1]))
        with mock.patch.object(server_module.time, 'monotonic', return_value=102.0):
            self.assertTrue(self.publish([dict(marker(), player_id=1)]))
            self.assertNotIn('gun_marker', self.state._public_player(
                self.state.players[1]))
            self.assertTrue(self.admit())
            self.assertTrue(self.publish([dict(marker(2), player_id=1)]))
            self.assertIn('gun_marker', self.state._public_player(
                self.state.players[1]))


class ServerAimClientWireTests(unittest.TestCase):
    def active_client(self):
        return client_fixtures.ProjectileWireTests().active_client()

    def send(self, client, value):
        return client.send_input(
            0.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0,
            pose_time_us=10, shell_index=0, next_shell_index=0,
            shell_change_pending=False, gun_checkpoint=_gun_checkpoint(),
            gun_aim_checkpoint=value)

    def test_client_attaches_native_checkpoint_to_the_same_ordered_input(self):
        client = self.active_client()
        sent = []
        with mock.patch.object(client, '_send', side_effect=lambda value:
                               sent.append(value) or True):
            self.assertTrue(self.send(client, checkpoint()))
        self.assertEqual(1, sent[0]['input_seq'])
        self.assertEqual(checkpoint(), sent[0]['gun_aim_checkpoint'])
        self.assertNotIn('shot_origin', sent[0])
        self.assertNotIn('gun_aim_checkpoint_seq', sent[0])

    def test_invalid_local_checkpoint_does_not_consume_an_input_sequence(self):
        client = self.active_client()
        with mock.patch.object(client, '_send', return_value=True) as send:
            self.assertFalse(self.send(client, checkpoint(gun_pitch=True)))
            self.assertEqual(0, client._input_seq)
            send.assert_not_called()
            self.assertTrue(self.send(client, checkpoint()))
            self.assertEqual(1, client._input_seq)

    def test_player_reader_accepts_delayed_samples_and_rejects_broken_pairs(self):
        valid = {'input_seq': 3, 'gun_aim_checkpoint_seq': 3,
                 'gun_aim_checkpoint': checkpoint(), 'gun_marker': marker(2)}
        self.assertTrue(lan_client._valid_player_gun_aim_contract(valid))
        self.assertTrue(lan_client._valid_player_gun_aim_contract({}))
        for change in ({'gun_aim_checkpoint_seq': 2}, {'gun_marker': marker(4)},
                       {'gun_marker': marker(2, direction=[0, 0, 0])},
                       {'gun_aim_checkpoint': None}):
            self.assertFalse(lan_client._valid_player_gun_aim_contract(
                dict(valid, **change)))
        del valid['gun_aim_checkpoint_seq']
        self.assertFalse(lan_client._valid_player_gun_aim_contract(valid))

    def test_worker_marker_is_frozen_in_existing_state_batch_without_a_new_barrier(self):
        client = client_fixtures.ProjectileWireTests().active_worker_client()
        samples = [dict(marker(), player_id=7)]
        self.assertTrue(client.send_projected_bot_state(
            [], edge_sample_time_us=0, edge_revision=1,
            player_gun_markers=samples))
        samples[0]['origin'][0] = 999.0
        queued = client._dequeue_outbound(client._transport_generation)
        payload = client_fixtures.wire_copy(queued[1])
        self.assertEqual('bot_state', payload['type'])
        self.assertEqual(client.round_id, payload['round_id'])
        self.assertEqual(client.authority_epoch, payload['authority_epoch'])
        self.assertEqual([dict(marker(), player_id=7)],
                         payload['player_gun_markers'])
