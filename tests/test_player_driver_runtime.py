"""Behavior at the real BattleRuntime driver seam; no native product starts."""
import copy
import sys
import types
import unittest
from unittest import mock

import tests.test_port_0922_battle_runtime as fixtures
from player_driver_fixture import local_player, room_message
from test_driver_state import _Runtime as _ReceiptMirror
from test_port_0922_server_projectiles import _state, _gun_checkpoint
from gui.mods.offline_lan_0922 import lan_client, vehicle_physics


BattleRuntime = fixtures.BattleRuntime
runtime_module = fixtures.battle_runtime_module


class PlayerDriverRuntimeTests(unittest.TestCase):
    def driver_pipeline(self):
        """Real runtime receipts, LAN serializer and server admission."""
        state = _state(players=1)
        state._logical_motion_time_us = lambda *unused: 10000000
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(copy.deepcopy(message)) or True)
        runtime = fixtures._runtime()
        runtime.bigworld.now = 10.0
        hidden = BattleRuntime(runtime)
        hidden._player_driver_mode = True
        hidden._pose_motion_time_us = 10000000
        hidden._pose_motion_local_time = 10.0
        hidden._local_position = (0.0, 0.0, 0.0)
        hidden._local_speed = 10.0
        hidden._local_surface_up_cosine = 1.0
        hidden._server = types.SimpleNamespace(vehicle_id=101)
        hidden._sender = runtime_module._LANInputSender(hidden)
        hidden._sender.forward = 1.0
        receipts = []
        hidden.client = types.SimpleNamespace(
            player_id=1, _input_seq=0,
            publish_driver_state=lambda row: (
                receipts.append(copy.deepcopy(row)) or True))
        visible = _ReceiptMirror()
        visible._local_position = (0.0, 0.0, 0.0)
        visible._local_yaw = visible._local_pitch = visible._local_roll = 0.0
        visible._local_speed = 10.0
        visible._local_surface_up_cosine = 1.0
        visible.local_pose = lambda: (visible._local_position, visible._local_yaw)
        for name in ('local_ram_contacts', 'local_destructible_contacts',
                     '_ram_contacts_enqueued', '_destructible_contacts_enqueued'):
            setattr(visible, name, types.MethodType(getattr(BattleRuntime, name), visible))
        visible._local_siege_braking = None
        visible._gun_last_tick = None
        visible._gun_state = types.SimpleNamespace(
            shot_index=0, pending_index=None, reload_time=0.0,
            reload_duration=5.0, clip=1, clip_size=1, dispersion=0.02)
        client = lan_client.LANClient('127.0.0.1', 28782, 'Human', 'ussr:R11_MS-1')
        client.ready = True
        client.phase = 'battle'
        client.player_id = 1
        client.round_id = state.round_id
        client.capabilities = tuple(state.players[1].capabilities)
        client.server_capabilities = tuple(state.players[1].capabilities)
        wire = []

        def dispatch(message):
            wire.append(copy.deepcopy(message))
            self.assertEqual('input', message['type'])
            return state.update_input(1, message)

        client._send = dispatch
        visible.client = client
        visible._sender = runtime_module._LANInputSender(visible)
        visible._sender.forward = 1.0
        self.assertTrue(client.send_input(
            1.0, 0.0, position=(0.0, 0.0, 0.0), yaw=0.0, speed=10.0,
            pose_time_us=9750000, shell_index=0, next_shell_index=0,
            shell_change_pending=False, gun_checkpoint=_gun_checkpoint()))
        return hidden, visible, state, receipts, wire, relayed

    def verify_worker_sweep(self, player, contact, wrong_token=False):
        """Run the real worker resolver up to its native geometry boundary."""
        runtime = fixtures._runtime()
        worker = BattleRuntime(runtime)
        worker._worker_mode = True
        worker._avatar = runtime.bigworld.avatar
        worker.client = fixtures._Client()
        worker.client.send_player_destructible_contact_result = mock.Mock(return_value=True)
        token = contact['token'] if not wrong_token else [[999, 999, 1]]
        proposal = {'status': 'crushed', 'token': token, 'requires_commit': True}
        worker._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value=proposal),
            _catalog_motion_blocked=mock.Mock(return_value=proposal))
        worker._destructible_pose_sweep = mock.Mock(return_value=proposal)
        worker._resolve_player_descriptor = mock.Mock(return_value=fixtures._Descriptor())
        row = dict(player, effective_params=fixtures._effective_params_snapshot(),
                   destructible_contacts=[contact])
        authority_name = 'gui.mods.offline_lan_0922.destructibles_authority'
        authority = types.SimpleNamespace(is_destroyed=lambda *unused: False)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(package, 'destructibles_authority', authority, create=True):
            self.assertEqual(1, worker._resolve_player_destructible_contacts([row], 10.0))
        worker.client.send_player_destructible_contact_result.assert_called_once_with(
            1, contact['seq'], not wrong_token, contact['token'])
        if contact['end_yaw'] != contact['yaw']:
            before = (contact['x'], contact['y'], contact['z'])
            after = (contact['end_x'], contact['end_y'], contact['end_z'])
            call = worker._destructible_pose_sweep.call_args_list[0]
            self.assertEqual((before, contact['yaw'], after, contact['end_yaw']), call.args[:4])
        else:
            call = worker._destructibles._catalog_motion_proposal.call_args
            self.assertEqual((contact['x'], contact['y'], contact['z']),
                             runtime_module._xyz(call.args[1]))
            self.assertEqual(contact['yaw'], call.args[2])
            self.assertEqual(contact['speed'], call.args[3])
            self.assertEqual(contact['dt'], call.kwargs['dt'])
        return worker

    def frame_battle(self, hidden=False, paired=False, elapsed=0.25):
        runtime = fixtures._runtime()
        runtime.bigworld.now = 2.0
        battle = fixtures.BattleRuntimeContractTests._live_frame_battle(None, runtime)
        battle._last_frame_time = 2.0 - elapsed
        battle._player_driver_mode = hidden
        battle._player_driver = (types.SimpleNamespace(ready=True)
                                 if paired else None)
        battle._send_driver_control = mock.Mock()
        battle._consume_driver_receipts = mock.Mock()
        battle._advance_local_player_burst = mock.Mock()
        return battle

    def test_hidden_positive_player_never_owns_projectiles(self):
        battle = BattleRuntime(fixtures._runtime())
        battle.client = types.SimpleNamespace(player_id=3,
                                              is_bot_authority=lambda: True)
        battle._player_driver_mode = True
        self.assertFalse(battle._projectile_is_authority())
        for kind in ('human', 'bot'):
            self.assertFalse(battle._owns_projectile(
                {'shooter_kind': kind, 'shooter_id': 3}))
        battle._player_driver_mode = False
        self.assertTrue(battle._projectile_is_authority())

    def test_hidden_start_binds_real_positive_player_and_no_driver_frontend(self):
        runtime = fixtures._runtime()
        # The common fake treats compact bytes as a type name. Resolve the
        # mounted fixture through its normal STRING name for native creation.
        runtime.vehicles.VehicleDescr = lambda **unused: fixtures._VehicleDescr(
            typeName='ussr:R11_MS-1')
        battle = BattleRuntime(runtime)
        client = fixtures._Client()
        client.player_id = 3
        message = fixtures._minimal_start()
        message['players'][0]['id'] = 3
        with mock.patch.object(battle, '_garage_item',
                               side_effect=AssertionError('hidden garage read')) as garage, \
                mock.patch.object(battle, '_prepare_bot_vehicle_assignments',
                                  return_value=True):
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Human', 'player_driver_mode': True,
            }, message, client))
        self.assertTrue(battle._player_driver_mode)
        self.assertFalse(battle._worker_mode)
        self.assertIsNone(battle._player_driver)
        self.assertFalse(battle._projectile_is_authority())
        self.assertEqual(3, battle._local_state()['id'])
        garage.assert_not_called()
        battle.stop(show_login=False, restore_account=False)

    def test_visible_pair_consumes_receipts_without_local_drive_and_keeps_burst(self):
        battle = self.frame_battle(paired=True)
        battle._frame()
        battle._fail.assert_not_called()
        battle._drive_local.assert_not_called()
        battle._advance_local_player_burst.assert_called_once_with()
        battle._send_driver_control.assert_called_once_with()
        battle._consume_driver_receipts.assert_called_once_with()

    def test_hidden_frame_passes_the_whole_late_interval_to_motion_not_gun(self):
        battle = self.frame_battle(hidden=True, elapsed=1.25)
        battle._frame()
        battle._fail.assert_not_called()
        battle._drive_local.assert_called_once_with(1.25)
        battle._advance_local_player_burst.assert_not_called()
        battle._send_driver_control.assert_not_called()
        battle._tick_drowning.assert_called_once_with(1.25, 2.0)
        battle._tick_overturn.assert_called_once_with(1.25, 2.0)

    def test_unpaired_visible_retains_motion_and_burst(self):
        battle = self.frame_battle()
        battle._frame()
        battle._fail.assert_not_called()
        battle._drive_local.assert_called_once_with(0.25)
        battle._advance_local_player_burst.assert_called_once_with()

    def test_hidden_real_elapsed_driver_substeps_publish_only_final_pose(self):
        battle = BattleRuntime(fixtures._runtime())
        battle._player_driver_mode = True
        battle._sender = types.SimpleNamespace(send_current=mock.Mock())
        battle._server = object()
        battle._drive_local_step = mock.Mock(return_value=False)
        battle._publish_driver_state = mock.Mock(return_value=True)
        battle._drive_local(1.25)
        steps = [call.args[0] for call in battle._drive_local_step.call_args_list]
        self.assertEqual(13, len(steps))
        self.assertAlmostEqual(1.25, sum(steps))
        self.assertTrue(all(0 < step <= 0.1 for step in steps))
        battle._publish_driver_state.assert_called_once_with()
        battle._sender.send_current.assert_not_called()

    def test_hidden_native_mailboxes_cannot_replace_private_control(self):
        battle = BattleRuntime(fixtures._runtime())
        battle._player_driver_mode = True
        sender = runtime_module._LANInputSender(battle)
        sender.forward = 0.75
        sender.turn = -1.0
        sender.aim_yaw = 0.6
        sender.gun_pitch = -0.2
        sender.handbrake = True
        before = dict(sender.__dict__)
        battle.shoot = mock.Mock()
        battle._echo_local_gun_angles = mock.Mock()
        sender.send_current = mock.Mock()
        before.pop('send_current', None)
        for kind, payload in (
                ('move', {'flags': 1}), ('cruise', {'mode': 0}),
                ('track_world', {'point': (10.0, 2.0, 40.0)}),
                ('track_relative', {'point': (1.0, 2.0, 3.0)}),
                ('stop_tracking', {'turret_yaw': 1.0, 'gun_pitch': 0.8}),
                ('shoot', {})):
            self.assertFalse(sender.send_avatar_input(101, kind, payload))
            after = dict(sender.__dict__)
            after.pop('send_current', None)
            self.assertEqual(before, after)
        sender.send_current.assert_not_called()
        battle.shoot.assert_not_called()
        battle._echo_local_gun_angles.assert_not_called()

    def test_readiness_waits_for_driver_then_sends_once(self):
        battle = BattleRuntime(fixtures._runtime())
        battle._battle_live = False
        battle._start_message = {'players': [{'id': 3}], 'bots': []}
        battle._records = {'player:3': {'kind': 'player', 'ready': True}}
        battle.client = types.SimpleNamespace(send_battle_ready=mock.Mock(return_value=True))
        battle._player_driver = types.SimpleNamespace(ready=False)
        battle._report_lineup_windows = mock.Mock()
        self.assertFalse(battle._maybe_send_battle_ready())
        battle.client.send_battle_ready.assert_not_called()
        battle._player_driver.ready = True
        self.assertTrue(battle._maybe_send_battle_ready())
        self.assertFalse(battle._maybe_send_battle_ready())
        self.assertEqual(1, battle.client.send_battle_ready.call_count)

    def test_failed_driver_before_ready_does_not_hold_room_countdown(self):
        for frontend_error, body_error in (('closed', None), (None, 'invalid receipt')):
            with self.subTest(frontend_error=frontend_error, body_error=body_error):
                battle = BattleRuntime(fixtures._runtime())
                frontend = types.SimpleNamespace(ready=False, error=frontend_error)
                battle._player_driver = frontend
                battle._driver_error = body_error
                battle._battle_live = False
                battle._start_message = {'players': [{'id': 3}], 'bots': []}
                battle._records = {'player:3': {'kind': 'player', 'ready': False}}
                battle.client = types.SimpleNamespace(send_battle_ready=mock.Mock(return_value=True))
                battle._report_lineup_windows = mock.Mock()
                self.assertFalse(battle._maybe_send_battle_ready())
                battle._records['player:3']['ready'] = True
                self.assertTrue(battle._maybe_send_battle_ready())
                self.assertFalse(battle._maybe_send_battle_ready())
                battle.client.send_battle_ready.assert_called_once()
                self.assertIs(frontend, battle._player_driver)
                # The separate visible-frame gate still selects receipt
                # consumption rather than restoring local body integration.
                paired = self.frame_battle(paired=True)
                paired._player_driver = frontend
                paired._driver_error = body_error
                paired._frame()
                paired._drive_local.assert_not_called()
                paired._advance_local_player_burst.assert_called_once_with()

    def test_hidden_loadout_uses_bound_positive_player_not_garage_or_first_row(self):
        battle = BattleRuntime(fixtures._runtime())
        battle._player_driver_mode = True
        selected = local_player(3)
        unrelated = local_player(1)
        unrelated['vehicle_compact_descr'] = 'd3Jvbmc='
        battle.client = types.SimpleNamespace(player_id=3)
        battle._start_message = {'players': [unrelated, selected]}
        battle._garage_item = mock.Mock(side_effect=AssertionError('hidden garage read'))
        result = battle._garage_loadout_snapshot()
        self.assertEqual((b'test', selected['vehicle']), result['fitting'])
        self.assertEqual(dict(selected['effective_params']['ammo']), result['shells'])
        self.assertEqual((), result['personal_mission_ids'])
        battle._garage_item.assert_not_called()
        selected['vehicle_compact_descr'] = 'cmVwbGFjZWQ='
        self.assertIs(result, battle._garage_loadout_snapshot())

    def test_actual_message_handlers_forward_each_barrier_before_dispatch(self):
        battle = BattleRuntime(fixtures._runtime())
        battle.state = 'stopped'
        frontend = types.SimpleNamespace(forward_message=mock.Mock(return_value=True))
        battle._player_driver = frontend
        handlers = (('roster', battle.on_roster), ('snapshot', battle.on_snapshot),
                    ('events', battle.on_events), ('battle_live', battle.on_battle_live))
        for kind, handler in handlers:
            message = room_message(kind)
            handler(message)
            self.assertIs(message, frontend.forward_message.call_args.args[0])
        self.assertEqual(4, frontend.forward_message.call_count)

    def test_stop_unbinds_once_even_without_completed_startup(self):
        battle = BattleRuntime(fixtures._runtime())
        frontend = types.SimpleNamespace(close=mock.Mock())
        battle._player_driver = frontend
        battle.stop(show_login=False, restore_account=False)
        self.assertIsNone(battle._player_driver)
        battle.stop(show_login=False, restore_account=False)
        frontend.close.assert_called_once_with('battle_stop')

    def test_sender_preserves_repeated_pose_none_while_publishing_input_and_gun(self):
        battle = BattleRuntime(fixtures._runtime())
        battle.client = fixtures._Client()
        battle._player_driver = object()
        battle.local_pose = mock.Mock(return_value=((12.0, 1.0, 24.0), 0.8))
        battle._send_driver_control = mock.Mock()
        battle._driver_pose_publication = mock.Mock(side_effect=[
            ((12.0, 1.0, 24.0), 123456), (None, None)])
        battle._driver_pose_published = mock.Mock()
        sender = runtime_module._LANInputSender(battle)
        sender.forward, sender.turn = 0.75, -1.0
        sender.aim_yaw, sender.gun_pitch = 0.6, -0.2
        self.assertTrue(sender.send_current())
        self.assertTrue(sender.send_current())
        published = [row for row in battle.client.sent if row[0] == 'input']
        self.assertEqual((12.0, 1.0, 24.0), published[0][1][4])
        self.assertEqual(123456, published[0][2]['pose_time_us'])
        self.assertIsNone(published[1][1][4])
        self.assertNotIn('pose_time_us', published[1][2])
        self.assertEqual((0.75, -1.0, 0.6, -0.2), published[1][1][:4])
        self.assertEqual(2, battle._driver_pose_published.call_count)

    def test_substep_translation_receipts_preserve_server_and_worker_sweep(self):
        hidden, visible, state, receipts, wire, relayed = self.driver_pipeline()
        proposed = []

        def integrate(dt):
            start = hidden._local_position
            end = (start[0], start[1], start[2] + 10.0 * dt)
            seq = len(proposed) + 1
            self.assertTrue(hidden._queue_local_destructible_contact(
                {'requires_commit': True, 'token': [[7, seq, 1]]},
                start, 0.0, 10.0, dt, end_position=end, end_yaw=0.0))
            proposed.append(copy.deepcopy(hidden._local_destructible_contacts[seq]))
            hidden._send_pending_local_destructible_contacts_at_pose(start, 0.0)
            hidden._local_position = end
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.25)
        self.assertEqual([9850000, 9950000, 10000000, 10000000],
                         [row['pose_time_us'] for row in receipts])
        self.assertEqual([1, 2, 3, 4], [row['sample_seq'] for row in receipts])
        self.assertEqual([1.0, 2.0, 2.5, 2.5],
                         [row['state']['position'][2] for row in receipts])
        self.assertEqual(9750000, hidden._driver_integration_start_us)
        visible._player_driver.receipts = receipts
        self.assertTrue(visible._consume_driver_receipts())
        player = state.players[1]
        self.assertEqual([9750000, 9850000, 9950000, 10000000],
                         [row['time_us'] for row in player.pose_history])
        self.assertEqual([1, 2, 3, 4],
                         [row['input_seq'] for row in player.pose_history])
        self.assertEqual(5, player.input_seq)
        self.assertNotIn('x', wire[-1])
        self.assertNotIn('pose_time_us', wire[-1])
        self.assertEqual(5, visible._driver_published_input_seq)
        for expected, relay in zip(proposed, relayed):
            contact = relay['player']['destructible_contacts'][0]
            for key in ('x', 'y', 'z', 'yaw', 'end_x', 'end_y', 'end_z', 'end_yaw'):
                self.assertEqual(expected[key], contact[key], key)
            self.assertEqual(expected['seq'] + 1, contact['input_seq'])
            self.assertEqual(receipts[expected['seq'] - 1]['pose_time_us'],
                             contact['pose_time_us'])
            self.assertEqual(((expected['x'], expected['y'], expected['z']), expected['yaw']),
                             visible._local_destructible_safe_poses[expected['seq']])
            self.verify_worker_sweep(relay['player'], contact)
        self.assertEqual(3, len(relayed))
        # A foreign token still reaches a local rejected worker outcome.
        self.verify_worker_sweep(relayed[0]['player'],
                                 relayed[0]['player']['destructible_contacts'][0],
                                 wrong_token=True)

    def test_substep_track_pivot_keeps_exact_source_arc_after_server_admission(self):
        hidden, visible, state, receipts, wire, relayed = self.driver_pipeline()
        physics = state.players[1].effective_params['physics']
        physics['rotationIsAroundCenter'] = False
        hidden._local_physics = dict(physics)
        hidden._local_speed = 0.0
        hidden._sender.forward = visible._sender.forward = 0.0
        hidden._sender.turn = visible._sender.turn = 1.0
        proposed = []

        def integrate(dt):
            start, yaw = hidden._local_position, hidden._local_yaw
            end_yaw = yaw + 0.6 * dt
            end = vehicle_physics.track_pivot_position(start, yaw, end_yaw,
                                                      physics['trackCenter'])
            seq = len(proposed) + 1
            self.assertTrue(hidden._queue_local_destructible_contact(
                {'requires_commit': True, 'token': [[8, seq, 1]]},
                start, yaw, 0.0, dt, end_position=end, end_yaw=end_yaw))
            proposed.append(copy.deepcopy(hidden._local_destructible_contacts[seq]))
            hidden._send_pending_local_destructible_contacts_at_pose(start, yaw)
            hidden._local_position, hidden._local_yaw = end, end_yaw
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.25)
        visible._player_driver.receipts = receipts
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(3, len(relayed))
        for expected, relay in zip(proposed, relayed):
            contact = relay['player']['destructible_contacts'][0]
            before = tuple(contact[name] for name in ('x', 'y', 'z'))
            after = tuple(contact[name] for name in ('end_x', 'end_y', 'end_z'))
            self.assertEqual(tuple(expected[name] for name in ('x', 'y', 'z')), before)
            self.assertEqual(tuple(expected[name] for name in ('end_x', 'end_y', 'end_z')), after)
            self.assertEqual(expected['yaw'], contact['yaw'])
            self.assertEqual(expected['end_yaw'], contact['end_yaw'])
            self.assertAlmostEqual(physics['trackCenter'],
                vehicle_physics.track_pivot_from_poses(
                    physics, before, contact['yaw'], after, contact['end_yaw']))
            self.verify_worker_sweep(relay['player'], contact)
        self.assertEqual([9750000, 9850000, 9950000, 10000000],
                         [row['time_us'] for row in state.players[1].pose_history])
        self.assertNotIn('pose_time_us', wire[-1])

    def test_deferred_siege_ack_binds_completed_substep_and_real_room_input(self):
        hidden, visible, state, receipts, wire, unused_relay = self.driver_pipeline()
        hidden._local_speed = 0.0
        hidden._sender.forward = visible._sender.forward = 0.0
        state.players[1].vehicle = 'sweden:S21_UDES_03'
        entity = types.SimpleNamespace(siegeState=0)
        hidden._report_local_siege_edge = mock.Mock()
        steps = []

        def integrate(dt):
            steps.append(dt)
            if len(steps) == 2:
                self.assertTrue(hidden._send_local_siege_request(entity, True))
                self.assertTrue(hidden._driver_integrating_step)
                self.assertEqual([], receipts)
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.25)
        self.assertEqual([9950000, 10000000], [row['pose_time_us'] for row in receipts])
        self.assertEqual(1, hidden._driver_siege_sample_seq)
        self.assertTrue(receipts[0]['siege_enabled'])
        self.assertNotIn('siege_enabled', receipts[1])
        visible._player_driver.receipts = receipts
        self.assertTrue(visible._consume_driver_receipts())
        controls = visible._player_driver.controls
        first_ack = next(row for row in controls if row['published_sample_seq'] == 1)
        self.assertEqual(2, first_ack['published_input_seq'])
        # Exercise the actual hidden ACK parser with a valid runtime entity.
        hidden._server_entity = lambda unused: visible.entity
        hidden._avatar = visible._avatar
        hidden._apply_mirrored_gun_pose = mock.Mock()
        first_ack['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(first_ack))
        self.assertEqual((True, 2), hidden._local_siege_pending)
        self.assertEqual(0, hidden._driver_siege_sample_seq)
        self.assertEqual(3, state.players[1].input_seq)

    def test_translation_then_pivot_in_one_slice_preserves_two_distinct_sweeps(self):
        hidden, visible, state, receipts, wire, relayed = self.driver_pipeline()
        physics = state.players[1].effective_params['physics']
        physics['rotationIsAroundCenter'] = False
        proposed = []

        def integrate(dt):
            start = hidden._local_position
            translated = (0.0, 0.0, 10.0 * dt)
            end_yaw = 0.6 * dt
            rotated = vehicle_physics.track_pivot_position(
                translated, 0.0, end_yaw, physics['trackCenter'])
            for seq, before, after, speed, final_yaw in (
                    (1, start, translated, 10.0, 0.0),
                    (2, translated, rotated, 0.0, end_yaw)):
                self.assertTrue(hidden._queue_local_destructible_contact(
                    {'requires_commit': True, 'token': [[9, seq, 1]]},
                    before, 0.0, speed, dt, end_position=after, end_yaw=final_yaw))
                proposed.append(copy.deepcopy(hidden._local_destructible_contacts[seq]))
                hidden._send_pending_local_destructible_contacts_at_pose(before, 0.0)
            hidden._local_position, hidden._local_yaw = rotated, end_yaw
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.1)
        visible._player_driver.receipts = receipts
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(2, len(relayed))
        for expected, relay in zip(proposed, relayed):
            contact = relay['player']['destructible_contacts'][0]
            for key in ('x', 'y', 'z', 'yaw', 'end_x', 'end_y', 'end_z', 'end_yaw'):
                self.assertEqual(expected[key], contact[key], key)
            self.assertEqual(2, contact['input_seq'])
            self.assertEqual(10000000, contact['pose_time_us'])
            self.verify_worker_sweep(relay['player'], contact)
        self.assertEqual([9750000, 10000000],
                         [row['time_us'] for row in state.players[1].pose_history])
        self.assertNotIn('x', wire[-1])

    def test_impossible_driver_contact_is_rejected_without_rejecting_body_pose(self):
        hidden, visible, state, receipts, unused_wire, relayed = self.driver_pipeline()

        def integrate(dt):
            start = hidden._local_position
            self.assertTrue(hidden._queue_local_destructible_contact(
                {'requires_commit': True, 'token': [[7, 1, 1]]},
                start, 0.0, 10.0, dt, end_position=(0.0, 0.0, 100.0), end_yaw=0.0))
            hidden._send_pending_local_destructible_contacts_at_pose(start, 0.0)
            hidden._local_position = (0.0, 0.0, 1.0)
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.1)
        visible._player_driver.receipts = receipts
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(1.0, state.players[1].z)
        self.assertEqual([], relayed)
        self.assertIn(1, state.players[1].destructible_contact_rejections)
        self.assertFalse(state.players[1].destructible_contacts)
        self.assertIsNone(visible._driver_error)
        self.assertEqual('battle', state.phase)

    def test_receipt_failure_stops_remaining_physical_substeps_only(self):
        hidden, unused_visible, unused_state, unused_receipts, unused_wire, unused_relay = self.driver_pipeline()
        hidden.client.publish_driver_state = lambda unused: False
        steps = []

        def integrate(dt):
            steps.append(dt)
            hidden._local_position = (0.0, 0.0, hidden._local_position[2] + 10.0 * dt)
            hidden._publish_driver_state()
            return False

        hidden._drive_local_step = integrate
        hidden._drive_local(0.25)
        self.assertEqual([0.1], steps)
        self.assertEqual((0.0, 0.0, 1.0), hidden._local_position)
        self.assertIn('receipt send failed', hidden._driver_error)


if __name__ == '__main__':
    unittest.main()
