import copy
import json
import unittest
from unittest import mock

from test_port_0922_server_projectiles import _state
import test_port_0922_lan_client_projectiles as client_fixtures
from test_port_0922_lan_client_queue import QueueBigWorld
from gui.mods.offline_lan_0922 import turret_detachment
from gui.mods.offline_lan_0922 import turret_obstacle_schema as schema
from gui.mods.offline_lan_0922 import lan_client as client_module
from gui.mods.offline_lan_0922.authority_worker import AuthorityWorkerLANClient
from lan_battle_server import SIMULATION_WORKER_AUTHORITY_ID


def proposal(actor_id=2, actor_kind='player'):
    flight = turret_detachment.resolve_flight(
        (0.0, 2.0, 0.0), (1.0, 10.5, 0.0),
        lambda start, end: (end[0], 0.0, end[2]) if end[1] <= 0 else None)
    return {'actor_kind': actor_kind, 'actor_id': actor_id,
            'flight': flight, 'attitude': [0.0, 0.0, 0.0],
            'spin': [1.2, 1.4, 1.6]}


def record(actor_id=2, actor_kind='player'):
    return dict(schema.normalize_proposal(proposal(actor_id, actor_kind)),
                created_time_ms=1000)


def state_with_wreck():
    state = _state(players=4)
    state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
    state.bot_roster = []
    state.players[2].health = 0
    state.players[2].alive = False
    state.players[2].critical['ammo_rack_death'] = True
    return state


def publish(state, rows=None, **changes):
    message = {'type': 'bot_state', 'round_id': state.round_id,
               'authority_epoch': state.authority_epoch,
               'rows': [[entry['id']] for entry in state.bot_manifest]}
    if rows is not None:
        message['detached_turrets'] = rows
    message.update(changes)
    return state.update_bot_states(SIMULATION_WORKER_AUTHORITY_ID, message)


class TurretObstacleSchemaTests(unittest.TestCase):
    def test_real_flight_round_trips_without_a_fabricated_clock(self):
        row = proposal()
        normalized = schema.normalize_proposal(row)
        self.assertEqual(json.loads(json.dumps(row)), normalized)
        self.assertIsNone(schema.normalize_record(row))
        self.assertEqual('player:2', schema.row_key(normalized))
        normalized['flight']['origin'][0] = 99
        self.assertEqual(0.0, row['flight']['origin'][0])

    def test_unlanded_flight_has_no_contact_or_invented_landing(self):
        row = proposal()
        row['flight'] = turret_detachment.resolve_flight(
            (0, 2, 0), (1, 10.5, 0), lambda start, end: None)
        normalized = schema.normalize_proposal(row)
        self.assertIsNotNone(normalized)
        self.assertFalse(normalized['flight']['landed'])
        self.assertIsNone(normalized['flight']['contact'])

    def test_bad_scalar_vectors_segments_and_missing_fields_are_local(self):
        changes = [
            ('actor_id', True), ('actor_id', 1.5), ('actor_id', -1),
            ('actor_kind', 'human'), ('spin', [0, 0]),
            ('attitude', [float('nan'), 0, 0]),
            ('attitude', [float('inf'), 0, 0]),
            ('spin', ['1', 0, 0]),
        ]
        for name, value in changes:
            with self.subTest(name=name, value=value):
                self.assertIsNone(schema.normalize_proposal(
                    dict(proposal(), **{name: value})))
        for name, value in (
                ('origin', [5001, 0, 0]), ('velocity', [3001, 0, 0]),
                ('duration', 8.1), ('segments', []), ('landed', 1),
                ('energy', -1), ('contact', None)):
            row = proposal()
            row['flight'][name] = value
            self.assertIsNone(schema.normalize_proposal(row), name)
        row = proposal()
        row['flight']['segments'] *= 5
        self.assertIsNone(schema.normalize_proposal(row))
        for name in proposal():
            row = proposal()
            del row[name]
            self.assertIsNone(schema.normalize_proposal(row), name)
        for value in (True, -1, 1.5, float('inf')):
            self.assertIsNone(schema.normalize_record(
                dict(record(), created_time_ms=value)))


class TurretObstacleServerTests(unittest.TestCase):
    def test_accepts_dead_human_and_bot_with_server_owned_immutable_clock(self):
        state = state_with_wreck()
        state.bot_manifest = [{'id': 17}]
        state.bot_states[17] = {
            'id': 17, 'team': 2, 'health': 0, 'alive': False,
            'critical': {'ammo_rack_death': True}}
        first = proposal()
        first['created_time_ms'] = -100
        self.assertTrue(publish(state, [first, proposal(17, 'bot')]))
        accepted = state._detached_turret_snapshot()
        self.assertEqual({'player:2', 'bot:17'}, set(state.detached_turrets))
        self.assertTrue(all(row['created_time_ms'] == state._server_time_ms()
                            for row in accepted))
        changed = proposal()
        changed['flight']['rest'] = [90, 1, 0]
        state.tick += 1
        self.assertTrue(publish(state, [changed, proposal(17, 'bot')]))
        self.assertEqual(accepted, state._detached_turret_snapshot())
        accepted[0]['flight']['rest'][0] = -999
        self.assertNotEqual(accepted, state._detached_turret_snapshot())

    def test_invalid_rows_live_actors_and_unknown_actors_do_not_reject_frame(self):
        state = state_with_wreck()
        self.assertTrue(publish(state, [
            None, {}, proposal(1), proposal(20), proposal(17, 'bot'),
            proposal()]))
        self.assertEqual(['player:2'], list(state.detached_turrets))
        self.assertEqual('', state.last_bot_state_reject)

    def test_missing_empty_and_malformed_sections_do_not_erase_accepted_rows(self):
        state = state_with_wreck()
        self.assertTrue(publish(state, [proposal()]))
        accepted = state._detached_turret_snapshot()
        for value in (None, [], {}, 'bad'):
            self.assertTrue(publish(state, value))
            self.assertEqual(accepted, state._detached_turret_snapshot())

    def test_round_sender_and_epoch_gate_admission_without_rejecting_good_state(self):
        state = state_with_wreck()
        self.assertTrue(publish(state, [proposal()], authority_epoch=0))
        self.assertFalse(state.detached_turrets)
        self.assertTrue(publish(state, [proposal()], authority_epoch=None))
        self.assertFalse(state.detached_turrets)
        self.assertFalse(publish(state, [proposal()], round_id=state.round_id - 1))
        self.assertFalse(state.update_bot_states(1, {
            'round_id': state.round_id, 'authority_epoch': state.authority_epoch,
            'rows': [], 'detached_turrets': [proposal()]}))
        self.assertFalse(state.detached_turrets)
        self.assertTrue(publish(state, [proposal()]))
        self.assertEqual(1, len(state.detached_turrets))
        state._reset_round()
        self.assertFalse(state.detached_turrets)
        self.assertFalse(publish(state, [proposal()], round_id=state.round_id - 1))

    def test_final_wreck_can_be_admitted_after_result_freezes_combat(self):
        state = state_with_wreck()
        state.battle_result = {'winner': 1}
        self.assertTrue(publish(state, [proposal()]))
        self.assertEqual(1, len(state.detached_turrets))

    def test_snapshot_loading_and_late_join_replay_the_same_accepted_records(self):
        state = state_with_wreck()
        self.assertTrue(publish(state, [proposal()]))
        accepted = state._detached_turret_snapshot()
        self.assertEqual(accepted, state.current_battle_message()['detached_turrets'])
        messages = []
        state.simulation_worker.offer_reliable = lambda row: messages.append(row) or True
        state.simulation_worker.offer_snapshot = lambda row: messages.append(row) or True
        state.tick_once(1.0 / 30.0)
        snapshot = next(row for row in messages if row['type'] == 'snapshot')
        self.assertEqual(accepted, snapshot['detached_turrets'])
        state.phase = 'loading'
        self.assertEqual(accepted, state.loading_snapshot()['detached_turrets'])

    def test_capacity_is_shared_and_does_not_replace_earlier_wrecks(self):
        state = state_with_wreck()
        state.bot_manifest = [{'id': identity} for identity in range(1, 14)]
        state.bot_states = {identity: {
            'id': identity, 'team': 2, 'health': 0, 'alive': False,
            'critical': {'ammo_rack_death': True}} for identity in range(1, 14)}
        self.assertTrue(publish(state, [proposal(identity, 'bot')
                                      for identity in range(1, 13)]))
        first = state._detached_turret_snapshot()
        self.assertTrue(publish(state, [proposal(13, 'bot')]))
        self.assertEqual(schema.MAX_ACTIVE_TURRETS, len(first))
        self.assertEqual(first, state._detached_turret_snapshot())


class TurretObstacleClientTests(unittest.TestCase):
    def client(self):
        client = client_fixtures.ProjectileWireTests().active_client()
        client.authority_epoch = 2
        client.bigworld = QueueBigWorld()
        return client

    @staticmethod
    def snapshot(tick, **changes):
        message = client_fixtures.ProjectileWireTests.player_snapshot(tick, [])
        message.update(changes)
        return message

    def test_only_server_records_survive_snapshot_normalization_and_rewrite(self):
        client = self.client()
        first = record()
        client._handle_message(self.snapshot(1, detached_turrets=[
            proposal(1), None, first]))
        self.assertTrue(client.running, client.last_error)
        self.assertEqual([first], client.last_snapshot['detached_turrets'])
        rewrite = copy.deepcopy(first)
        rewrite['flight']['rest'] = [90, 1, 0]
        for tick, fields in enumerate(({}, {'detached_turrets': []},
                                      {'detached_turrets': [rewrite]}), 2):
            client._handle_message(self.snapshot(tick, **fields))
            self.assertEqual([first], client.last_snapshot['detached_turrets'])
        client._handle_message(self.snapshot(8, authority_epoch=1,
                                            detached_turrets=[record(3)]))
        self.assertEqual([first], client.last_snapshot['detached_turrets'])

    def test_poll_preserves_first_accepted_record_across_lean_snapshot_coalescing(self):
        client = self.client()
        client._pending = [self.snapshot(1, detached_turrets=[record()]),
                           self.snapshot(2, detached_turrets=[]),
                           self.snapshot(3)]
        client._poll()
        self.assertTrue(client.running, client.last_error)
        self.assertEqual(3, client.last_snapshot['server_tick'])
        self.assertEqual([record()], client.last_snapshot['detached_turrets'])

    def test_receive_pressure_keeps_the_latest_turret_barrier(self):
        client = self.client()
        first = self.snapshot(1, detached_turrets=[record()])
        second = self.snapshot(2)
        first.pop('bot_manifest')
        second.pop('bot_manifest')
        client._pending = [first, second]
        with mock.patch.object(client_module, 'MAX_PENDING_MESSAGES', 2):
            client._queue_message(self.snapshot(3))
        self.assertIs(first, client._pending[0])
        self.assertEqual([1, 3], [row['server_tick'] for row in client._pending])

    def test_new_round_discards_old_records_and_same_round_barrier_retains_them(self):
        client = self.client()
        original = client._adopt_detached_turrets({
            'round_id': 3, 'detached_turrets': [record()]})
        same = client._adopt_detached_turrets({'round_id': 3})
        self.assertEqual(original, same)
        self.assertNotIn('detached_turrets', client._adopt_detached_turrets({
            'round_id': 4}))

    def test_base_senders_and_worker_override_preserve_proposals(self):
        for method in ('send_bot_state', 'send_projected_bot_state'):
            worker = client_fixtures.ProjectileWireTests().active_worker_client()
            sender = (getattr(client_module.LANClient, method)
                      if method == 'send_projected_bot_state' else
                      client_module.LANClient.send_bot_state)
            self.assertTrue(sender(worker, [], detached_turrets=[None, proposal()]))
            message = client_fixtures.wire_copy(worker._outbound_queue[-1][1])
            self.assertEqual(worker.authority_epoch, message['authority_epoch'])
            self.assertEqual([schema.normalize_proposal(proposal())],
                             message['detached_turrets'])
        worker = client_fixtures.ProjectileWireTests().active_worker_client()
        row = proposal()
        self.assertTrue(worker.send_projected_bot_state(
            [], edge_sample_time_us=1, edge_revision=1, detached_turrets=[row]))
        first = client_fixtures.wire_copy(worker._outbound_queue[-1][1])
        row['spin'][0] = 9
        self.assertTrue(worker.send_projected_bot_state(
            [], edge_sample_time_us=1, edge_revision=1))
        self.assertEqual(2, len(worker._outbound_queue))
        self.assertEqual([schema.normalize_proposal(proposal())],
                         first['detached_turrets'])
        self.assertEqual(worker.authority_epoch, first['authority_epoch'])


if __name__ == '__main__':
    unittest.main()
