import unittest
from test_port_0922_turret_obstacles import row
from gui.mods.offline_lan_0922 import turret_contact_ledger as ledger


class TurretContactLedgerTests(unittest.TestCase):
    def test_coalescing_acks_retries_and_stale_frames_preserve_momentum(self):
        sent = {}
        ledger.record(sent, 'bot:17', (1, 2, 3), (4, 5, 6))
        first = sent['bot:17'][:]
        ledger.record(sent, 'bot:17', (-1, 4, 0), (0, 0, 2))
        latest = sent['bot:17']
        self.assertEqual((0, 6, 3, 4, 5, 8), ledger.unseen(latest, None))
        self.assertEqual((-1, 4, 0, 0, 0, 2), ledger.unseen(latest, first))
        self.assertEqual((0,)*6, ledger.unseen(first, latest))
        self.assertEqual((0,)*6, ledger.pending(sent, 'bot:17', [['player:1']+latest[1:]], 'player:1'))
        self.assertEqual(tuple(latest[2:]), ledger.pending(sent, 'bot:17', [['player:2']+latest[1:]], 'player:1'))

    def test_malformed_frame_cannot_poison_authority(self):
        for invalid in (None, [[{}, 1]+[0]*6], [['bot:17', True]+[0]*6],
                        [['bot:17', 1, float('nan')]+[0]*5],
                        [['bot:17', 1]+[0]*5], [['bot:17', 1]+[0]*6]*2):
            with self.assertRaises((ValueError, TypeError, OverflowError)):
                ledger.normalize(invalid)

    def test_server_only_relays_known_actor_and_newest_checkpoint(self):
        from test_port_0922_server_projectiles import _state, _gun_checkpoint
        state = _state(players=1)
        state.detached_turrets['bot:17'] = row()
        player = state.players[1]
        def send(rows):
            return state.update_input(1, {
                'type': 'input', 'round_id': state.round_id,
                'input_seq': player.input_processed_seq+1,
                'pose_time_us': state._logical_motion_time_us(),
                'forward': 1, 'turn': 0, 'speed': 0,
                'x': player.x, 'y': player.y, 'z': player.z,
                'yaw': 0, 'pitch': 0, 'roll': 0, 'fire_seq': 0,
                'aim_yaw': 0, 'gun_pitch': 0,
                'shell_index': 0, 'next_shell_index': 0,
                'shell_change_pending': False,
                'gun_checkpoint': _gun_checkpoint(), 'turret_pushes': rows})
        latest = ['bot:17', 9, 100, 0, 20, 0, 50, 0]
        self.assertTrue(send([latest, ['bot:18', 2]+[0]*6]))
        self.assertEqual([latest], state._public_player(player)['turret_pushes'])
        self.assertTrue(send([['bot:17', 3]+[0]*6]))
        self.assertEqual([latest], state._public_player(player)['turret_pushes'])
