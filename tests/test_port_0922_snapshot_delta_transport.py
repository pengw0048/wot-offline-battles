import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))

import lan_battle_server as server
from gui.mods.offline_lan_0922 import snapshot_delta
from test_port_0922_simulation_worker import (
    _BlockingConnection, _Connection, _wait_until)
import test_port_0922_snapshot_budget_guard as budget_tests


def snapshot(tick, health=100, x=0.0):
    return {
        'type': 'snapshot', 'round_id': 1, 'authority_epoch': 1,
        'map': '01_karelia', 'bot_authority_id': -1,
        'server_tick': tick, 'server_time_ms': tick * 33,
        'players': [],
        'bots': [{'id': 1000, 'health': health, 'x': x,
                  'critical': {'devices': {'engine': 1}}}],
    }


class SnapshotDeltaTransportTests(unittest.TestCase):
    def test_replaced_candidate_diffs_against_completed_inflight_write(self):
        connection = _BlockingConnection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player._force_async_outbox = True
        try:
            self.assertTrue(player.offer_snapshot(snapshot(1)))
            self.assertTrue(connection.started.wait(1.0))
            self.assertTrue(player.offer_snapshot(snapshot(2, health=60)))
            self.assertTrue(player.offer_snapshot(snapshot(3, health=60, x=5)))
            self.assertEqual(0, player._snapshot_sequence)
            connection.release.set()
            _wait_until(lambda: player._snapshot_sequence == 2)
            self.assertEqual([1, 3], [m['server_tick']
                                     for m in connection.messages])
            first, baseline = snapshot_delta.decode(connection.messages[0], None)
            last, unused = snapshot_delta.decode(connection.messages[1], baseline)
            self.assertEqual(snapshot(1), first)
            self.assertEqual(snapshot(3, health=60, x=5), last)
            self.assertTrue(connection.messages[1]['snapshot_delta'])
            self.assertEqual(1, connection.messages[1]['snapshot_base_seq'])
        finally:
            connection.release.set()
            player.disconnect()

    def test_reliable_full_rebases_in_actual_event_and_snapshot_order(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        self.assertTrue(player.send(snapshot(1)))
        player._force_async_outbox = True
        try:
            with mock.patch.object(player, '_start_outbox_locked'):
                player.offer_snapshot(snapshot(2, health=80))
                player.offer_reliable({
                    'type': 'events', 'round_id': 1, 'server_tick': 3,
                    'events': [{'kind': 'hit'}]})
                player.offer_reliable(snapshot(3, health=50))
                player.offer_snapshot(snapshot(4, health=50, x=8))
            with player._outbox_condition:
                player._start_outbox_locked()
            _wait_until(lambda: player._snapshot_sequence == 3)
            self.assertEqual(['snapshot', 'events', 'snapshot', 'snapshot'],
                             [m['type'] for m in connection.messages])
            full = connection.messages[2]
            delta = connection.messages[3]
            self.assertFalse(full['snapshot_delta'])
            self.assertEqual(2, delta['snapshot_base_seq'])
            unused, baseline = snapshot_delta.decode(full, None)
            restored, unused = snapshot_delta.decode(delta, baseline)
            self.assertEqual(snapshot(4, health=50, x=8), restored)
        finally:
            player.disconnect()

    def test_delayed_candidate_freezes_nested_caller_state(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player._force_async_outbox = True
        message = snapshot(1)
        expected = copy.deepcopy(message)
        try:
            with mock.patch.object(player, '_start_outbox_locked'):
                player.offer_snapshot(message)
            message['bots'][0]['critical']['devices']['engine'] = 0
            with player._outbox_condition:
                player._start_outbox_locked()
            _wait_until(lambda: player._snapshot_sequence == 1)
            restored, unused = snapshot_delta.decode(connection.messages[0], None)
            self.assertEqual(expected, restored)
        finally:
            player.disconnect()

    def test_failed_write_does_not_advance_baseline(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player.offer_snapshot(snapshot(1))
        baseline = player._snapshot_baseline
        connection.sendall = mock.Mock(side_effect=BrokenPipeError('closed'))
        with mock.patch.object(server, '_server_log'):
            self.assertFalse(player.offer_snapshot(snapshot(2, health=25)))
        self.assertFalse(player.connected)
        self.assertIs(baseline, player._snapshot_baseline)
        self.assertEqual(1, player._snapshot_sequence)

    def test_opt_in_wire_profile_reports_actual_completed_payload_bytes(self):
        connection = _Connection()
        with mock.patch.dict(os.environ, {'WOT_OFFLINE_COMBAT_PROFILE': '1'}):
            player = server.Player(1, connection, ('127.0.0.1', 1))
        player._snapshot_wire_profile[0] = 0.0
        with mock.patch.object(server.time, 'monotonic', return_value=5.0), \
                mock.patch.object(server, '_server_log') as log:
            self.assertTrue(player.offer_snapshot(snapshot(1)))
        payload_bytes = len((json.dumps(
            connection.messages[0], separators=(',', ':')) + '\n').encode())
        self.assertIn('delta=0 full=1 bytes=%d ' % payload_bytes,
                      log.call_args[0][0])
        self.assertIn('SNAPSHOT_WIRE role=player endpoint=1 elapsed=5.000',
                      log.call_args[0][0])

    def test_wire_profile_io_failure_does_not_change_delivery(self):
        with mock.patch.dict(os.environ, {'WOT_OFFLINE_COMBAT_PROFILE': '1'}):
            player = server.Player(1, _Connection(), ('127.0.0.1', 1))
        player._snapshot_wire_profile[0] = 0.0
        with mock.patch.object(server.time, 'monotonic', return_value=5.0), \
                mock.patch.object(server, '_server_log', side_effect=OSError('log')):
            self.assertTrue(player.offer_snapshot(snapshot(1)))
        self.assertTrue(player.connected)
        self.assertEqual(1, player._snapshot_sequence)

    def test_lifecycle_barrier_forces_next_snapshot_full(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player.offer_snapshot(snapshot(1))
        player.send({'type': 'roster', 'round_id': 1})
        player.offer_snapshot(snapshot(2, health=75))
        self.assertFalse(connection.messages[-1]['snapshot_delta'])
        self.assertEqual(2, connection.messages[-1]['snapshot_seq'])

    def test_delta_preserves_monotonic_wire_clock(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player.offer_snapshot(snapshot(5))
        second = snapshot(6, x=9)
        second['server_time_ms'] = 1
        player.offer_snapshot(second)
        self.assertEqual(165, connection.messages[-1]['server_time_ms'])
        unused, baseline = snapshot_delta.decode(connection.messages[0], None)
        restored, unused = snapshot_delta.decode(connection.messages[-1], baseline)
        self.assertEqual(9, restored['bots'][0]['x'])
        self.assertEqual(165, restored['server_time_ms'])

    def test_cached_reliable_clock_is_clamped_after_newer_snapshot_write(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        older = {'type': 'events', 'round_id': 1, 'server_tick': 4,
                 'server_time_ms': 100, 'events': []}
        cached = player._serialize_message(older)
        self.assertTrue(player._write_message(snapshot(5)))
        self.assertTrue(player._write_message(older, cached))
        self.assertEqual([165, 165], [m['server_time_ms']
                                     for m in connection.messages])

    def test_metadata_budget_overflow_keeps_complete_state_and_connection(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        message = snapshot(1)
        message['padding'] = ''
        raw_size = len((json.dumps(message, separators=(',', ':')) + '\n').encode())
        message['padding'] = 'x' * (server.MAX_LINE_BYTES - raw_size)
        with mock.patch.object(server, '_server_log') as log:
            self.assertTrue(player.offer_snapshot(message))
        self.assertTrue(player.connected)
        self.assertEqual(message, connection.messages[-1])
        self.assertIsNone(player._snapshot_baseline)
        log.assert_not_called()
        self.assertTrue(player.offer_snapshot(snapshot(2, x=3)))
        self.assertFalse(connection.messages[-1]['snapshot_delta'])

    def test_invalid_local_actor_falls_back_without_killing_writer(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player.offer_snapshot(snapshot(1))
        invalid = snapshot(2)
        invalid['bots'][0]['id'] = None
        self.assertTrue(player.offer_snapshot(invalid))
        self.assertEqual(invalid, connection.messages[-1])
        self.assertIsNone(player._snapshot_baseline)
        self.assertTrue(player.offer_snapshot(snapshot(3)))
        self.assertFalse(connection.messages[-1]['snapshot_delta'])

    def test_repair_resets_sparse_frontiers_after_older_inflight_write(self):
        connection = _BlockingConnection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player._force_async_outbox = True
        older = snapshot(1)
        older.update(bot_orders=[], bot_order_revision=7,
                     destructibles=[], destructible_revision=9)
        try:
            player.offer_snapshot(older)
            self.assertTrue(connection.started.wait(1.0))
            player.offer_snapshot_repair(snapshot(2))
            connection.release.set()
            _wait_until(lambda: player._snapshot_sequence == 2)
            self.assertEqual(-1, player.bot_order_revision_sent)
            self.assertEqual(-1, player.destructible_revision_sent)
            self.assertFalse(connection.messages[-1]['snapshot_delta'])
        finally:
            connection.release.set()
            player.disconnect()

    def test_page_prepared_before_repair_cannot_hide_missing_prefix(self):
        connection = _Connection()
        player = server.Player(1, connection, ('127.0.0.1', 1))
        player.destructible_revision_sent = 80
        stale_page = snapshot(3)
        stale_page.update(destructibles=[{'revision': 81}],
                          destructible_revision=81,
                          destructible_base_revision=80)
        player.offer_snapshot_repair(snapshot(2))
        player.offer_snapshot(stale_page)
        self.assertNotIn('destructibles', connection.messages[-1])
        self.assertEqual(-1, player.destructible_revision_sent)
        replay_page = snapshot(4)
        replay_page.update(destructibles=[{'revision': 17}],
                           destructible_revision=17,
                           destructible_base_revision=0)
        player.offer_snapshot(replay_page)
        self.assertEqual([{'revision': 17}],
                         connection.messages[-1]['destructibles'])
        self.assertEqual(17, player.destructible_revision_sent)
        self.assertFalse(player._destructible_replay_required)

    def test_resync_sends_reliable_manifest_full_only_to_requester(self):
        state, player, connection = (
            budget_tests.SnapshotBudgetGuardTests()._state(base_bytes=1))
        state.tick_once(1.0 / server.TICK_HZ)
        state.tick_once(1.0 / server.TICK_HZ)
        state.tick_once(1.0 / server.TICK_HZ)
        self.assertTrue(connection.messages[-1]['snapshot_delta'])
        self.assertTrue(state.request_snapshot_resync(player))
        state.tick_once(1.0 / server.TICK_HZ)
        self.assertFalse(connection.messages[-1]['snapshot_delta'])
        self.assertIn('bot_manifest', connection.messages[-1])
        self.assertFalse(player.snapshot_resync_requested)
        self.assertTrue(player.connected)
        repair_index = len(connection.messages)
        state.tick_once(1.0 / server.TICK_HZ)
        state.tick_once(1.0 / server.TICK_HZ)
        subsequent = connection.messages[repair_index:]
        self.assertTrue(any('bot_orders' in message for message in subsequent))
        self.assertTrue(any('destructibles' in message for message in subsequent))
        stale = server.Player(player.player_id, _Connection(), ('127.0.0.1', 2))
        self.assertFalse(state.request_snapshot_resync(stale))


if __name__ == '__main__':
    unittest.main()
