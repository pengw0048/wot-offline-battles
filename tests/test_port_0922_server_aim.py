"""Worker geometry, stock marker selection and trigger-bound aim evidence."""

import contextlib
import copy
import io
import math
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixture
from gui.mods.offline_lan_0922 import server_aim


def checkpoint():
    return {'position': [10., 20., 30.], 'rotation': [0., 0., 0.],
            'turret_yaw': 0., 'gun_pitch': 0., 'dispersion_angle': .02}


class ServerAimTests(unittest.TestCase):
    def battle(self):
        runtime = fixture._runtime()
        battle = fixture.BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.gunRotator.showServerMarker = True
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle.client = types.SimpleNamespace(player_id=2, authority_epoch=4)
        battle._start_message = {'round_id': 7}
        return battle, runtime

    def test_native_fake_pins_read_only_property_and_in_place_feedback(self):
        battle, unused = self.battle()
        rotator = battle._avatar.gunRotator
        local = rotator._VehicleGunRotator__dispersionAngles
        with self.assertRaises(AttributeError):
            rotator.dispersionAngle = .5
        battle._avatar.updateGunMarker(10, None, None, .02)
        self.assertIs(local, rotator._VehicleGunRotator__dispersionAngles)
        self.assertEqual([.02, .01], local)
        self.assertEqual(.02, rotator.dispersionAngle)

    def _reply(self, battle, angle=.02):
        sample = server_aim.sample(fixture._Descriptor(), checkpoint(), 8, 625.)
        sample['dispersion_angle'] = angle
        battle._last_snapshot = {
            'round_id': 7, 'authority_epoch': 4,
            'players': [{'id': battle.client.player_id, 'gun_marker': sample}]}

    def test_reply_then_immediate_fire_keeps_local_dispersion_with_either_switch(self):
        case = fixture.BattleRuntimeContractTests()
        for enabled in (False, True):
            for client_mode in (False, True):
                for local_angle, reply_angle in ((.25, .02), (.02, .25)):
                    with self.subTest(enabled=enabled, client_mode=client_mode,
                                      local=local_angle, reply=reply_angle):
                        battle, gun, unused, client, unused_record = (
                            case._pending_fire_shell_change_battle())
                        client.authority_epoch = 4
                        rotator = battle._avatar.gunRotator
                        rotator.showServerMarker = enabled
                        rotator.clientMode = client_mode
                        local = [local_angle, .005]
                        rotator._VehicleGunRotator__dispersionAngles = local
                        self._reply(battle, reply_angle)
                        before = (list(gun.ammo), gun.clip, gun.reload_time)
                        self.assertEqual(enabled, battle._sync_local_server_marker())
                        self.assertIs(local, rotator._VehicleGunRotator__dispersionAngles)
                        self.assertEqual([local_angle, .005], local)
                        self.assertEqual(before, (gun.ammo, gun.clip, gun.reload_time))
                        if enabled:
                            self.assertEqual(reply_angle, battle._avatar.gun_marker_updates[-1][3])
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertTrue(battle.shoot(0., 0.))
                        message = [row for row in client.sent if row[0] == 'input'][-1]
                        self.assertEqual(local_angle, message[2]['gun_aim_checkpoint']['dispersion_angle'])
                        fire = [row for row in client.sent if row[0] == 'fire_intent'][-1]
                        self.assertEqual(local_angle, fire[2]['dispersion_angle'])

    def test_repeated_feedback_does_not_freeze_later_native_convergence(self):
        battle, unused = self.battle()
        rotator = battle._avatar.gunRotator
        self._reply(battle)
        for angle in (.25, .12, .04, .30):
            local = [angle, .003]
            rotator._VehicleGunRotator__dispersionAngles = local
            for unused_repeat in range(3):
                self.assertTrue(battle._sync_local_server_marker())
                self.assertIs(local, rotator._VehicleGunRotator__dispersionAngles)
                self.assertEqual([angle, .003], local)
                self.assertEqual(angle, battle._native_gun_aim_checkpoint()['dispersion_angle'])
        self.assertEqual([.02] * 12, [row[3] for row in battle._avatar.gun_marker_updates])

    def test_marker_exception_restores_source_before_optional_failure_is_contained(self):
        case = fixture.BattleRuntimeContractTests()
        battle, gun, unused, client, unused_record = case._pending_fire_shell_change_battle()
        client.authority_epoch = 4
        rotator = battle._avatar.gunRotator
        rotator.showServerMarker = True
        local = rotator._VehicleGunRotator__dispersionAngles
        self._reply(battle)
        original = battle._avatar.updateGunMarker

        def broken_marker(*args):
            original(*args)
            raise RuntimeError('native marker presentation failed')

        battle._avatar.updateGunMarker = broken_marker
        with contextlib.redirect_stdout(io.StringIO()):
            battle._ammo_tick()
            self.assertTrue(battle.shoot(0., 0.))
        self.assertEqual('running', battle.state)
        self.assertIsNone(battle.error)
        self.assertIs(local, rotator._VehicleGunRotator__dispersionAngles)
        self.assertEqual([.25, .01], local)
        self.assertEqual(.25, [row for row in client.sent if row[0] == 'fire_intent'][-1][2]['dispersion_angle'])

    def test_restoration_does_not_overwrite_a_new_native_list_or_rotator(self):
        for replace_rotator in (False, True):
            with self.subTest(replace_rotator=replace_rotator):
                battle, unused = self.battle()
                rotator = battle._avatar.gunRotator
                old = rotator._VehicleGunRotator__dispersionAngles
                self._reply(battle)
                original = battle._avatar.updateGunMarker
                newer = [.17, .004]

                def refresh(*args):
                    original(*args)
                    if replace_rotator:
                        battle._avatar.gunRotator = fixture._GunRotator(
                            _VehicleGunRotator__dispersionAngles=newer)
                    else:
                        rotator._VehicleGunRotator__dispersionAngles = newer

                battle._avatar.updateGunMarker = refresh
                self.assertTrue(battle._sync_local_server_marker())
                self.assertEqual([.25, .01], old)
                self.assertIs(newer, battle._avatar.gunRotator._VehicleGunRotator__dispersionAngles)
                self.assertEqual(.17, battle._native_dispersion_angle())

    def test_missing_or_invalid_native_list_never_enters_mutating_marker(self):
        for invalid in (None, (), (.25, .01), [], [.25], [.25, .01, .02]):
            with self.subTest(invalid=invalid):
                battle, unused = self.battle()
                self._reply(battle)
                battle._avatar.gunRotator._VehicleGunRotator__dispersionAngles = invalid
                battle._avatar.updateGunMarker = mock.Mock()
                with self.assertRaisesRegex(RuntimeError, 'dispersion storage'):
                    battle._sync_local_server_marker()
                battle._avatar.updateGunMarker.assert_not_called()

    def test_worker_geometry_uses_mounts_and_rotations_not_a_client_ray(self):
        descriptor = fixture._Descriptor()
        descriptor.hull.turretPositions = (fixture._Vector(1., 2., 3.),)
        descriptor.turret.gunPosition = fixture._Vector(4., 5., 6.)
        aim = checkpoint()
        aim['rotation'][0] = math.pi / 2.
        aim['turret_yaw'] = -math.pi / 2.
        sample = server_aim.sample(descriptor, aim, 8, 625.)
        for expected, actual in zip((17., 27.6, 35.), sample['origin']):
            self.assertAlmostEqual(expected, actual)
        for expected, actual in zip((0., 0., 1.), sample['direction']):
            self.assertAlmostEqual(expected, actual)
        self.assertEqual(.02, sample['dispersion_angle'])
        self.assertEqual(625., sample['shot_speed'])
        # Exact opposing yaw produces z=1.0000000000000002 in ordinary
        # double arithmetic; legal native geometry must survive wire bounds.
        aim['rotation'][0] = -3.088185578478767
        aim['turret_yaw'] = -aim['rotation'][0]
        self.assertEqual([0., 0., 1.], server_aim.sample(
            descriptor, aim, 9, 625.)['direction'])
        for invalid in (float('nan'), float('inf'), True, 10 ** 400):
            bad = dict(aim, dispersion_angle=invalid)
            self.assertIsNone(server_aim.canonical_checkpoint(bad))
            self.assertIsNone(server_aim.canonical_sample(
                dict(sample, shot_speed=invalid)))

    def test_native_checkpoint_preserves_hydraulic_pose_independent_of_setting(self):
        battle, unused = self.battle()
        matrix = fixture._Matrix()
        matrix.translation = fixture._Vector(1., 2., 3.)
        matrix.setRotateYPR((.4, .18, -.12))
        rotator = battle._avatar.gunRotator
        rotator.getAvatarOwnVehicleStabilisedMatrix = lambda: matrix
        rotator.turretYaw, rotator.gunPitch = .3, -.15
        rotator.getCurShotPosition = mock.Mock(side_effect=AssertionError(
            'a client ray is not an authority checkpoint'))
        first = battle._native_gun_aim_checkpoint()
        rotator.showServerMarker = False
        self.assertEqual(first, battle._native_gun_aim_checkpoint())
        self.assertEqual([1., 2., 3.], first['position'])
        self.assertAlmostEqual(.18, first['rotation'][1])
        self.assertAlmostEqual(.3, first['turret_yaw'])
        self.assertEqual(-.15, first['gun_pitch'])
        rotator.getCurShotPosition.assert_not_called()
        matrix.translation = fixture._Vector(float('nan'), 2., 3.)
        with self.assertRaises(RuntimeError):
            battle._native_gun_aim_checkpoint()

    def test_marker_switch_displays_only_reply_velocity_and_never_local_echo(self):
        battle, unused = self.battle()
        sample = server_aim.sample(fixture._Descriptor(), checkpoint(), 8, 625.)
        battle._last_snapshot = {'round_id': 7, 'authority_epoch': 4,
                                 'players': [{'id': 2, 'gun_marker': sample}]}
        rotator = battle._avatar.gunRotator
        rotator.getCurShotPosition = mock.Mock(side_effect=AssertionError(
            'server marker must not sample the current client ray'))
        self.assertEqual(.25, rotator.dispersionAngle)
        self.assertTrue(battle._sync_local_server_marker())
        call = battle._avatar.gun_marker_updates[-1]
        self.assertEqual((10., 21.5, 30.), tuple(call[1]))
        self.assertEqual((0., 0., 625.), tuple(call[2]))
        self.assertEqual(.02, call[3])
        rotator.showServerMarker = False
        self.assertFalse(battle._sync_local_server_marker())
        self.assertEqual(1, len(battle._avatar.gun_marker_updates))
        rotator.showServerMarker = True
        # A local queued shell cannot rewrite the accepted sample's speed.
        battle._gun_state = types.SimpleNamespace(shot_index=1, pending_index=0)
        self.assertTrue(battle._sync_local_server_marker())
        self.assertEqual((0., 0., 625.), tuple(
            battle._avatar.gun_marker_updates[-1][2]))
        rotator.getCurShotPosition.assert_not_called()

    def test_missing_stale_or_new_round_reply_hides_without_enabling_client(self):
        battle, unused = self.battle()
        sample = server_aim.sample(fixture._Descriptor(), checkpoint(), 8, 800.)
        snapshot = {'round_id': 7, 'authority_epoch': 4,
                    'players': [{'id': 2, 'gun_marker': sample}]}
        battle._last_snapshot = snapshot
        self.assertTrue(battle._sync_local_server_marker())
        for stale in (
                dict(snapshot, players=[{'id': 2}]),
                dict(snapshot, round_id=6), dict(snapshot, authority_epoch=3)):
            battle._last_snapshot = stale
            self.assertFalse(battle._sync_local_server_marker())
            self.assertTrue(battle._avatar.gunRotator.showServerMarker)
            self.assertEqual(False, battle._avatar.inputHandler.client_markers[-1])
            self.assertEqual(False, battle._avatar.inputHandler.server_markers[-1])
        self.assertEqual(1, len(battle._avatar.gun_marker_updates))
        battle._last_snapshot = snapshot
        self.assertTrue(battle._sync_local_server_marker())
        self.assertTrue(battle._avatar.inputHandler.server_markers[-1])
        # PREBATTLE retains its existing frozen native marker boundary.
        battle._battle_live = False
        self.assertFalse(battle._sync_local_server_marker())
        self.assertEqual(2, len(battle._avatar.gun_marker_updates))

    def test_worker_sampling_uses_loaded_shell_without_applying_gun_checkpoint(self):
        battle, runtime = self.battle()
        battle._worker_mode = True
        entity = fixture._Vehicle(11, fixture._Descriptor(), fixture._Vector(),
                                  (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = entity
        state = {'alive': True, 'input_seq': 8, 'gun_aim_checkpoint_seq': 8,
                 'gun_aim_checkpoint': checkpoint(), 'shell_index': 0,
                 'next_shell_index': 1, 'shell_change_pending': True}
        battle._records = {'player:2': {'kind': 'player', 'network_id': 2,
                                       'engine_id': 11, 'state': state}}
        gun = types.SimpleNamespace(_effective_params={'gun': {'shots': [
            {'source_shot': {'speed': 625.}}, {'source_shot': {'speed': 900.}}]}},
            reload_time=3., dispersion=.4, clip=1, ammo=[40, 10])
        battle._player_authority_guns = {2: gun}
        before = copy.deepcopy(gun.__dict__)
        marker = battle._worker_gun_markers()[0]
        self.assertEqual(625., marker['shot_speed'])
        self.assertEqual(8, marker['input_seq'])
        self.assertEqual(before, gun.__dict__)
        state.update(input_seq=9, gun_aim_checkpoint_seq=9, shell_index=1,
                     next_shell_index=1, shell_change_pending=False)
        self.assertEqual(900., battle._worker_gun_markers()[0]['shot_speed'])
        state['gun_aim_checkpoint_seq'] = 8
        self.assertEqual([], battle._worker_gun_markers())

    def test_stock_mode_callback_cannot_resurrect_a_missing_server_marker(self):
        battle, unused = self.battle()
        battle._last_snapshot = {'round_id': 7, 'authority_epoch': 4,
                                 'players': [{'id': 2}]}
        self.assertFalse(battle._sync_local_server_marker())
        handler = battle._avatar.inputHandler
        # A stock settings or mode callback may run between two ammo ticks.
        handler.showGunMarker2(True)
        self.assertFalse(battle._sync_local_server_marker())
        self.assertFalse(handler.server_markers[-1])
        self.assertFalse(handler.client_markers[-1])
        self.assertTrue(battle._avatar.gunRotator.showServerMarker)

    def test_unready_native_pose_preserves_movement_but_rejects_a_trigger(self):
        case = fixture.BattleRuntimeContractTests()
        battle, state, unused_settings, client, unused_record = (
            case._pending_fire_shell_change_battle())
        rotator = battle._avatar.gunRotator
        rotator.showServerMarker = False
        before = (list(state.ammo), state.clip, state.reload_time)
        for live, provider in (
                (False, mock.Mock(return_value=None)),
                (True, mock.Mock(side_effect=ReferenceError('not ready')))):
            battle._battle_live = live
            rotator.getAvatarOwnVehicleStabilisedMatrix = provider
            self.assertTrue(battle._sender.send_current())
            self.assertEqual('input', client.sent[-1][0])
            self.assertNotIn('gun_aim_checkpoint', client.sent[-1][2])
            self.assertEqual('running', battle.state)
        sent_before = len(client.sent)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(battle.shoot(0., 0.))
        self.assertIn('reason=gun_aim_unavailable', output.getvalue())
        self.assertEqual(sent_before, len(client.sent))
        self.assertIsNone(battle._local_fire_intent)
        self.assertEqual(before, (state.ammo, state.clip, state.reload_time))

        # Readiness can recover, even with the server reticle disabled. The
        # accepted trigger reuses its one captured pose in the input barrier.
        provider = mock.Mock(return_value=fixture._Matrix())
        rotator.getAvatarOwnVehicleStabilisedMatrix = provider
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(battle.shoot(0., 0.))
        provider.assert_called_once_with()
        input_message = [message for message in client.sent
                         if message[0] == 'input'][-1]
        self.assertIn('gun_aim_checkpoint', input_message[2])
        self.assertEqual('fire_intent', client.sent[-1][0])

    def test_a_stalled_snapshot_stream_cannot_keep_old_authority_aim_visible(self):
        battle, unused = self.battle()
        battle.client._snapshot_accepted_time = 10.
        battle._last_snapshot = {'round_id': 7, 'authority_epoch': 4,
            'players': [{'id': 2, 'gun_marker': server_aim.sample(
                fixture._Descriptor(), checkpoint(), 8, 625.)}]}
        with mock.patch.object(fixture.battle_runtime_module.lan_protocol,
                               '_monotonic_time', return_value=10.5):
            self.assertTrue(battle._sync_local_server_marker())
        with mock.patch.object(fixture.battle_runtime_module.lan_protocol,
                               '_monotonic_time', return_value=11.01):
            self.assertFalse(battle._sync_local_server_marker())
        self.assertTrue(battle._server_marker_waiting)
        self.assertEqual(1, len(battle._avatar.gun_marker_updates))

    def test_fire_uses_the_same_frozen_worker_geometry_before_newer_pose(self):
        battle, runtime = self.battle()
        battle._worker_mode = True
        battle._projectile_is_authority = lambda: True
        battle._config = {'perfect_accuracy': True}
        client = fixture._Client()
        client.authority_epoch = 4
        client.send_projectile_launch = mock.Mock(
            side_effect=lambda *args, **unused: args[2])
        battle.client = client
        entity = fixture._Vehicle(11, fixture._Descriptor(),
            fixture._Vector(80., 0., 80.), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = entity
        effective = fixture._effective_params_snapshot(ammo=[[101, 40]])
        effective['gun']['shots'][0]['source_shot']['speed'] = 625.
        battle._records = {'player:2': {'kind': 'player', 'network_id': 2,
            'engine_id': 11, 'state': {'alive': True, 'effective_params': effective}}}
        intent = {'type': 'fire_intent', 'round_id': 7, 'authority_epoch': 4,
            'player_id': 2, 'intent_seq': 3, 'shot_seq': 5, 'input_seq': 8,
            'pose_time_us': 1000, 'trigger_launch_time_ms': 900,
            'shell_index': 0, 'next_shell_index': 0, 'shell_change_pending': False,
            'gun_checkpoint_seq': 8, 'gun_checkpoint': fixture._human_gun_checkpoint(),
            'gun_aim_checkpoint_seq': 8, 'gun_aim_checkpoint': checkpoint(),
            'aim_yaw': .2, 'gun_pitch': -.1, 'x': 50., 'y': 0., 'z': 50.,
            'yaw': 0., 'pitch': 0., 'roll': 0., 'speed': 0.,
            'shot_origin': [4., 2., 8.], 'shot_direction': [.6, 0., .8],
            'dispersion_angle': .4, 'presentation_ledger': []}
        expected = server_aim.sample(entity.typeDescriptor, checkpoint(), 8, 625.)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(battle.on_fire_intent(intent))
            # The transport object is mutable; the accepted trigger is frozen.
            intent['gun_aim_checkpoint']['position'][0] = 900.
            self.assertTrue(battle.flush_admitted_player_fire_intents())
        call = client.send_projectile_launch.call_args
        self.assertEqual(expected['origin'], call.args[4])
        self.assertEqual([v * 625. for v in expected['direction']], call.args[5])
        self.assertEqual(8, call.kwargs['fire_input_seq'])


if __name__ == '__main__':
    unittest.main()
